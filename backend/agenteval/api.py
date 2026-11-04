import asyncio
import time
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agenteval import __version__
from agenteval.adapters import validate_agent_config
from agenteval.config import Settings
from agenteval.contracts import (AgentCreate, AgentVersionCreate, BenchmarkCreate,
                                BenchmarkVersionCreate, CompareCreate, Named, ProfileCreate,
                                RunCreate, digest)
from agenteval.db import (Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison,
                         EvaluationResult, Execution, GradingBatch, ProfileVersion,
                         Project, RequestDedup, Run, TestCase, Trial, make_database)
from agenteval.evaluators import CAPABILITIES, metadata, validate_case
from agenteval.limits import RequestLimit
from agenteval.service import LocalRunner, compare, records, report, snapshot
from agenteval.durable import Coordinator, enqueue
from agenteval.db import WorkItem, AuditEvent, JudgeCall, JudgeBudget, TraceSpan
from agenteval.health import LoopHealth, resilient_tick, status as health_status
from agenteval.contracts import ReleaseCapacity


def serialize(entity):
    return {column.name: getattr(entity, column.name) for column in entity.__table__.columns}


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = make_database(settings.database_url)
    runner = LocalRunner(sessions, settings)

    loop_health = LoopHealth("api-maintenance")
    maintenance = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal maintenance
        if len(settings.api_token) < 16:
            raise RuntimeError("Set AGENTEVAL_API_TOKEN to a random token of at least 16 characters")
        coordinator = runner.coordinator
        pump = None
        async def tick():
            nonlocal pump
            await asyncio.to_thread(coordinator.recover)
            if pump is not None and pump.done():
                previous, pump = pump, None
                previous.result()
            if settings.execution_mode == "local" and pump is None:
                with sessions() as session:
                    ids = list(session.scalars(select(WorkItem.id).where(
                        WorkItem.status.in_(["pending", "published"]),
                        WorkItem.created_at < datetime.fromtimestamp(time.time() - 2, timezone.utc)).limit(1)))
                if ids:
                    pump = asyncio.create_task(coordinator.drain())
        async def maintain():
            while True:
                delay = await resilient_tick(loop_health, tick, sessions)
                await asyncio.sleep(delay)
        await resilient_tick(loop_health, tick, sessions)
        maintenance = asyncio.create_task(maintain())
        try:
            yield
        finally:
            maintenance.cancel()
            if pump is not None:
                pump.cancel()
            await asyncio.gather(maintenance, *([pump] if pump else []), return_exceptions=True)
            engine.dispose()

    app = FastAPI(title="AgentEval", version=__version__, lifespan=lifespan,
                  description="Database-owned evaluation with fenced local/Celery workers.",
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions, app.state.engine = sessions, engine
    app.add_middleware(RequestLimit)

    @app.exception_handler(IntegrityError)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"code": "RESOURCE_CONFLICT", "message": "Resource version or key already exists"})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Do not echo raw input or credential-bearing request bodies.
        errors = [{"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "details": errors})

    def authorize(authorization: str = Header(default="")):
        if not settings.api_token or not secrets.compare_digest(authorization.encode(), f"Bearer {settings.api_token}".encode()):
            raise HTTPException(401, detail={"code": "UNAUTHORIZED"})

    def database():
        with sessions() as session:
            yield session

    def get(session, model, resource_id):
        entity = session.get(model, resource_id)
        if entity is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        return entity

    def page(session, model, offset, limit, **filters):
        query = select(model).filter_by(**filters).order_by(model.created_at.desc(), model.id)
        return {"items": [serialize(x) for x in session.scalars(query.offset(offset).limit(limit))],
                "offset": offset, "limit": limit,
                "total": session.scalar(select(func.count()).select_from(model).filter_by(**filters))}

    def add(session, entity):
        session.add(entity)
        session.commit()
        return serialize(entity)

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def readiness():
        try:
            detail = health_status(sessions, settings, loop_health, maintenance is not None and not maintenance.done())
            return JSONResponse(status_code=200 if detail["ready"] else 503, content={"ready": detail["ready"]})
        except Exception:
            return JSONResponse(status_code=503, content={"ready": False})

    @router.get("/operations/health")
    def operation_health():
        try:
            return health_status(sessions, settings, loop_health, maintenance is not None and not maintenance.done())
        except Exception:
            return JSONResponse(status_code=503, content={"ready": False, "database": "unavailable"})

    @router.get("/openapi.json")
    def openapi():
        return app.openapi()

    @router.get("/capabilities")
    def capabilities():
        return {"version": __version__, "execution_mode": settings.execution_mode,
                "adapters": ["http", "python", "langgraph"], "evaluators": list(CAPABILITIES),
                "automatic_retries": "read_only_only", "durable_queue": settings.execution_mode == "celery",
                "project_concurrency_limits": True, "langgraph": True, "llm_judge": True,
                "judge_configured": bool(settings.judge_endpoint and settings.judge_model),
                "pagination": "offset"}

    @router.post("/projects", status_code=201)
    def create_project(body: Named, session=Depends(database)):
        return add(session, Project(**body.model_dump()))

    @router.get("/evaluators")
    def evaluator_registry():
        from agenteval.contracts import EvaluatorSpec
        return {"items": [metadata(EvaluatorSpec.model_construct(id=key, config={})) for key in CAPABILITIES]}

    @router.get("/environments")
    def environment_registry():
        from agenteval.environment import ENVIRONMENTS
        return {"items": [{"id": key, "isolation": "per_execution", "initial_state": {}} for key in ENVIRONMENTS]}

    @router.get("/projects")
    def projects(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Project, offset, limit)

    @router.post("/agents", status_code=201)
    def create_agent(body: AgentCreate, session=Depends(database)):
        get(session, Project, body.project_id)
        return add(session, Agent(**body.model_dump()))

    @router.get("/agents")
    def agents(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), project_id: str | None = None, session=Depends(database)):
        return page(session, Agent, offset, limit, **({"project_id": project_id} if project_id else {}))

    @router.post("/agents/{agent_id}/versions", status_code=201)
    def create_agent_version(agent_id: str, body: AgentVersionCreate, session=Depends(database)):
        agent = get(session, Agent, agent_id)
        try:
            validate_agent_config(agent.adapter_type, body.configuration, settings)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "INVALID_AGENT_CONFIGURATION", "message": str(exc)})
        return add(session, AgentVersion(agent_id=agent_id, **body.model_dump(), content_hash=digest(body.model_dump())))

    @router.get("/agents/{agent_id}/versions")
    def agent_versions(agent_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        get(session, Agent, agent_id)
        return page(session, AgentVersion, offset, limit, agent_id=agent_id)

    @router.post("/benchmarks", status_code=201)
    def create_benchmark(body: BenchmarkCreate, session=Depends(database)):
        get(session, Project, body.project_id)
        return add(session, Benchmark(**body.model_dump()))

    @router.get("/benchmarks")
    def benchmarks(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), project_id: str | None = None, session=Depends(database)):
        return page(session, Benchmark, offset, limit, **({"project_id": project_id} if project_id else {}))

    @router.post("/benchmarks/{benchmark_id}/versions", status_code=201)
    def create_benchmark_version(benchmark_id: str, body: BenchmarkVersionCreate, session=Depends(database)):
        get(session, Benchmark, benchmark_id)
        cases = sorted([c.model_dump() for c in body.cases], key=lambda x: x["case_key"])
        version = BenchmarkVersion(benchmark_id=benchmark_id, version=body.version,
                                   content_hash=digest({"schema_version": "0.1", "cases": cases}), case_count=len(cases))
        session.add(version)
        session.flush()
        for case in cases:
            session.add(TestCase(benchmark_version_id=version.id, case_key=case["case_key"], definition=case))
        session.commit()
        return serialize(version)

    @router.get("/benchmarks/{benchmark_id}/versions")
    def benchmark_versions(benchmark_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        get(session, Benchmark, benchmark_id)
        return page(session, BenchmarkVersion, offset, limit, benchmark_id=benchmark_id)

    @router.get("/benchmark-versions/{version_id}/cases")
    def cases(version_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        get(session, BenchmarkVersion, version_id)
        return page(session, TestCase, offset, limit, benchmark_version_id=version_id)

    @router.post("/profile-versions", status_code=201)
    def create_profile(body: ProfileCreate, session=Depends(database)):
        get(session, Project, body.project_id)
        definition = body.model_dump(exclude={"project_id", "name", "description", "version"})
        return add(session, ProfileVersion(project_id=body.project_id, name=body.name, version=body.version,
                                          definition=definition, content_hash=digest(definition)))

    @router.get("/profile-versions")
    def profiles(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), project_id: str | None = None, session=Depends(database)):
        return page(session, ProfileVersion, offset, limit, **({"project_id": project_id} if project_id else {}))

    @router.post("/evaluations", status_code=202)
    def create_run(body: RunCreate, tasks: BackgroundTasks,
                   idempotency_key: str | None = Header(None, max_length=128), session=Depends(database)):
        version = get(session, AgentVersion, body.agent_version_id)
        agent = get(session, Agent, version.agent_id)
        benchmark_version = get(session, BenchmarkVersion, body.benchmark_version_id)
        benchmark = get(session, Benchmark, benchmark_version.benchmark_id)
        profile = get(session, ProfileVersion, body.profile_version_id)
        if len({agent.project_id, benchmark.project_id, profile.project_id}) != 1:
            raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        if idempotency_key:
            previous = session.scalar(select(RequestDedup).filter_by(project_id=agent.project_id, key_hash=digest(idempotency_key)))
            if previous:
                if previous.request_hash != digest(body.model_dump()):
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                return serialize(get(session, Run, previous.run_id))
        from agenteval.contracts import EvaluatorSpec
        specs = [EvaluatorSpec.model_validate(x) for x in profile.definition["evaluators"]]
        cases = records(session, TestCase, benchmark_version_id=benchmark_version.id)
        if agent.adapter_type == "component":
            from agenteval.components import COMPONENTS
            from jsonschema import Draft202012Validator
            component = COMPONENTS[version.configuration["plugin_id"]]
            for case in cases:
                if not Draft202012Validator(component["input_schema"]).is_valid(case.definition["input"]):
                    raise HTTPException(422, detail={"code": "COMPONENT_INPUT_INVALID", "case_key": case.case_key})
        core = set(profile.definition["gate"]["core_case_keys"])
        if core - {c.case_key for c in cases}:
            raise HTTPException(422, detail={"code": "UNKNOWN_CORE_CASE"})
        errors = []
        for spec in specs:
            for capability in metadata(spec)["required_capabilities"]:
                if capability not in version.capabilities:
                    errors.append({"evaluator": spec.instance_id, "code": "CAPABILITY_MISSING", "capability": capability})
            for case in cases:
                try:
                    validate_case(spec, case.definition)
                except (KeyError, ValueError, TypeError, IndexError):
                    errors.append({"case_key": case.case_key, "evaluator": spec.instance_id, "code": "INVALID_ASSERTIONS"})
        if errors:
            raise HTTPException(422, detail={"code": "PREFLIGHT_FAILED", "errors": errors[:100]})
        if profile.definition["execution"].get("tool_faults") and agent.adapter_type not in ("python", "langgraph"):
            raise HTTPException(422, detail={"code": "FAULT_INJECTION_REQUIRES_CONTROLLED_TOOL_BOUNDARY"})
        if profile.definition.get("environment"):
            if version.effects == "read_only" or profile.definition["execution"].get("max_retries", 0):
                raise HTTPException(422, detail={"code": "STATEFUL_ENVIRONMENT_REQUIRES_NO_RETRY"})
            if any(c.definition["assertions"].get("environment_initial", {}) != {} for c in cases):
                raise HTTPException(422, detail={"code": "INVALID_ENVIRONMENT_INITIAL_STATE"})
        if profile.definition["execution"].get("max_retries", 0) and version.effects != "read_only":
            raise HTTPException(422, detail={"code": "RETRY_REQUIRES_READ_ONLY"})
        judge_specs = [e for e in specs if e.id == "llm_judge"]
        if judge_specs and (not settings.judge_endpoint or not settings.judge_model):
            raise HTTPException(422, detail={"code": "JUDGE_NOT_CONFIGURED"})
        if len(judge_specs) * len(cases) * profile.definition["execution"]["repetitions"] > settings.judge_max_calls:
            raise HTTPException(422, detail={"code": "JUDGE_BUDGET_EXCEEDED"})
        run = Run(project_id=agent.project_id, **body.model_dump(), manifest={}, manifest_hash="")
        session.add(run)
        session.flush()
        session.add(JudgeBudget(run_id=run.id, max_calls=settings.judge_max_calls, policy_source="run_creation"))
        run.manifest = snapshot(session, run, settings)
        run.manifest_hash = digest(run.manifest)
        batch = GradingBatch(run_id=run.id, revision=0, grading_manifest={
            "parent_manifest_hash": run.manifest_hash, "judge": run.manifest["judge"], "engine_version": __version__, **profile.definition})
        session.add(batch)
        session.flush()
        for case in cases:
            for index in range(profile.definition["execution"]["repetitions"]):
                trial = Trial(run_id=run.id, case_id=case.id, repetition_index=index)
                session.add(trial)
                session.flush()
                execution = Execution(trial_id=trial.id)
                session.add(execution)
                session.flush()
                enqueue(session, run, "execute", execution.id)
        if idempotency_key:
            session.add(RequestDedup(project_id=agent.project_id, key_hash=digest(idempotency_key),
                                     request_hash=digest(body.model_dump()), run_id=run.id))
        run.status = "queued"
        receipt = session.info.pop("pending_onboarding_receipt", None)
        if receipt:
            from agenteval.db import OnboardingReceipt
            session.add(OnboardingReceipt(run_id=run.id, **receipt))
        session.commit()
        if settings.execution_mode == "local":
            tasks.add_task(runner.execute, run.id, batch.id)
        return {**serialize(run), "grading_batch_id": batch.id}

    @router.get("/evaluations")
    def runs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), project_id: str | None = None,
        q: str = Query("", max_length=200), status: str | None = Query(None, pattern="^(queued|running|evaluating|cancelling|completed|cancelled|failed)$"),
        quality: str | None = Query(None, pattern="^(pending|passed|failed|insufficient)$"), version: str | None = Query(None, max_length=120),
        created_after: datetime | None = None, created_before: datetime | None = None,
        sort: str = Query("newest", pattern="^(newest|oldest)$"), session=Depends(database)):
        normalize = lambda d: d.astimezone(timezone.utc) if d and d.tzinfo else d.replace(tzinfo=timezone.utc) if d else None
        created_after, created_before = normalize(created_after), normalize(created_before)
        if created_after and created_before and created_after > created_before:
            raise HTTPException(422, detail={"code": "INVALID_TIME_RANGE"})
        from agenteval.run_queries import page_runs
        return page_runs(session, serialize, project_id, offset, limit, q, status, quality, version, created_after, created_before, sort)

    @router.get("/evaluations/{run_id}")
    def run_detail(run_id: str, session=Depends(database)):
        run = get(session, Run, run_id)
        from agenteval.run_queries import summary_query, run_view
        query, _ = summary_query()
        return {**run_view(session.execute(query.where(Run.id == run.id)).mappings().one(), serialize),
            "grading_batches": [serialize(x) for x in records(session, GradingBatch, run_id=run_id)]}

    @router.post("/evaluations/{run_id}/cancel")
    def cancel(run_id: str, session=Depends(database)):
        run = get(session, Run, run_id)
        if run.status not in ("completed", "failed", "cancelled"):
            run.cancel_requested = True
            run.status = "cancelling"
            session.commit()
        return {"status": run.status, "in_flight_policy": "stop local process; HTTP remote outcome may remain unknown"}

    @router.post("/evaluations/{run_id}/regrade", status_code=202)
    def regrade(run_id: str, tasks: BackgroundTasks, session=Depends(database)):
        run = get(session, Run, run_id)
        if run.status not in ("completed", "cancelled"):
            raise HTTPException(409, detail={"code": "RUN_NOT_SETTLED"})
        batches = records(session, GradingBatch, run_id=run_id)
        if any(b.status in ("pending", "running") for b in batches):
            raise HTTPException(409, detail={"code": "GRADING_IN_PROGRESS"})
        revision = max(b.revision for b in batches) + 1
        batch = GradingBatch(run_id=run_id, revision=revision,
                             grading_manifest={**batches[0].grading_manifest, "engine_version": __version__})
        session.add(batch)
        session.flush()
        enqueue(session, run, "grade", batch.id)
        session.commit()
        if settings.execution_mode == "local":
            tasks.add_task(runner.grade, run_id, batch.id)
        return serialize(batch)

    def batch_for(session, run_id, batch_id):
        get(session, Run, run_id)
        batches = records(session, GradingBatch, run_id=run_id)
        batch = get(session, GradingBatch, batch_id) if batch_id else max(batches, key=lambda x: x.revision)
        if batch.run_id != run_id:
            raise HTTPException(404, detail={"code": "BATCH_NOT_IN_RUN"})
        return batch

    @router.get("/evaluations/{run_id}/report")
    def run_report(run_id: str, grading_batch_id: str | None = None, include_trials: bool = True,
                   trials_offset: int = Query(0, ge=0), trials_limit: int | None = Query(None, ge=1, le=100),
                   trial_filter: str = Query("all", pattern="^(all|bad)$"), session=Depends(database)):
        data = report(session, batch_for(session, run_id, grading_batch_id))
        rows = data["trials"] if trial_filter == "all" else [r for r in data["trials"] if r["verdict"] != "passed"]
        data["trials_total"] = len(rows)
        data["trials"] = rows[trials_offset:trials_offset + trials_limit] if trials_limit else rows
        if not include_trials:
            data["trials"] = []
        return data

    @router.get("/evaluations/{run_id}/results")
    def results(run_id: str, grading_batch_id: str | None = None, offset: int = Query(0, ge=0),
                limit: int = Query(50, ge=1, le=100), execution_id: str | None = None, session=Depends(database)):
        batch = batch_for(session, run_id, grading_batch_id)
        return {"grading_batch_id": batch.id,
                **page(session, EvaluationResult, offset, limit, batch_id=batch.id,
                       **({"execution_id": execution_id} if execution_id else {}))}

    @router.get("/evaluations/{run_id}/bad-cases")
    def bad_cases(run_id: str, grading_batch_id: str | None = None, session=Depends(database)):
        data = report(session, batch_for(session, run_id, grading_batch_id))
        return {"grading_batch_id": data["grading_batch_id"],
                "items": [x for x in data["trials"] if x["verdict"] != "passed"]}

    @router.get("/executions/{execution_id}")
    def execution_detail(execution_id: str, session=Depends(database)):
        return serialize(get(session, Execution, execution_id))

    @router.get("/traces/{trace_id}")
    def trace_detail(trace_id: str, session=Depends(database)):
        execution = session.scalar(select(Execution).filter_by(trace_id=trace_id))
        if not execution:
            raise HTTPException(404)
        response = execution.response or {}
        return {"trace_id": trace_id, "execution_id": execution.id,
                "observations": response.get("observations", {}),
                "complete_channels": response.get("observations_complete", []),
                "spans": [r.record for r in session.scalars(select(TraceSpan).filter_by(execution_id=execution.id))],
                "completeness": execution.trace_completeness,
                "format": "OpenTelemetry spans and structured observation channels"}

    @router.post("/regressions/compare", status_code=201)
    def create_comparison(body: CompareCreate, session=Depends(database)):
        baseline = get(session, GradingBatch, body.baseline_batch_id)
        candidate = get(session, GradingBatch, body.candidate_batch_id)
        for batch in (baseline, candidate):
            run = get(session, Run, batch.run_id)
            if batch.status != "completed" or run.status not in ("completed", "cancelled"):
                raise HTTPException(409, detail={"code": "RESULTS_NOT_FROZEN"})
        b_run, c_run = get(session, Run, baseline.run_id), get(session, Run, candidate.run_id)
        if b_run.project_id != c_run.project_id:
            raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        data = compare(session, baseline, candidate, body.mode)
        if body.mode == "strict" and not data["compatible"]:
            raise HTTPException(409, detail={"code": "INCOMPARABLE", "differences": data["differences"]})
        return add(session, Comparison(report=data))

    @router.get("/regressions/{comparison_id}")
    def comparison_detail(comparison_id: str, session=Depends(database)):
        from agenteval.comparison_context import views
        return views(session, [get(session, Comparison, comparison_id)], serialize)[0]

    @router.get("/regressions")
    def comparisons(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Comparison, offset, limit)

    @router.get("/evaluations/{run_id}/work-items")
    def work_items(run_id: str, session=Depends(database)):
        get(session, Run, run_id)
        return {"items": [serialize(x) for x in records(session, WorkItem, run_id=run_id)]}

    @router.get("/evaluations/{run_id}/audit")
    def audit(run_id: str, session=Depends(database)):
        get(session, Run, run_id)
        return {"items": [serialize(x) for x in records(session, AuditEvent, run_id=run_id)]}

    @router.get("/evaluations/{run_id}/judge-calls")
    def judge_calls(run_id: str, session=Depends(database)):
        get(session, Run, run_id)
        rows = session.scalars(select(JudgeCall).join(GradingBatch, JudgeCall.batch_id == GradingBatch.id).where(GradingBatch.run_id == run_id))
        return {"items": [serialize(x) for x in rows]}

    @router.post("/executions/{execution_id}/release-capacity")
    def release_capacity(execution_id: str, body: ReleaseCapacity, x_admin_token: str = Header(""), session=Depends(database)):
        if not settings.admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
            raise HTTPException(403, detail={"code": "ADMIN_REQUIRED"})
        execution = get(session, Execution, execution_id)
        trial = get(session, Trial, execution.trial_id)
        run = get(session, Run, trial.run_id)
        with runner.coordinator.locked(run.project_id) as locked:
            item = locked.scalar(select(WorkItem).filter_by(kind="execute", target_id=execution_id))
            if not item or item.status != "blocked":
                raise HTTPException(409, detail={"code": "NOT_BLOCKED"})
            item.status, item.epoch = "done", item.epoch + 1
            locked.add(AuditEvent(run_id=run.id, action="ADMIN_CAPACITY_RELEASED", detail={"execution_id": execution_id, "reason": body.reason}))
        return {"released": True, "verdict_unchanged": True, "execution_id": execution_id}

    from agenteval.extensions import install
    install(router, settings, sessions, database, get, serialize, create_run, runner)
    from agenteval.product import install as install_product
    install_product(router, settings, database, get, serialize, create_run, runner)
    app.include_router(router)
    # Hash routes stay in the browser, so API 404s never fall back to index.html.
    # Public static files contain no credentials or business data.
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def web_index():
            return FileResponse(frontend / "index.html")

    return app


app = create_app()
