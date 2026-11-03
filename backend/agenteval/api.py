import secrets
from contextlib import asynccontextmanager

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
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
from agenteval.evaluators import CAPABILITIES, validate_case
from agenteval.limits import RequestLimit
from agenteval.service import LocalRunner, compare, records, report, snapshot


def serialize(entity):
    return {column.name: getattr(entity, column.name) for column in entity.__table__.columns}


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = make_database(settings.database_url)
    runner = LocalRunner(sessions, settings)

    @asynccontextmanager
    async def lifespan(app):
        if len(settings.api_token) < 16:
            raise RuntimeError("Set AGENTEVAL_API_TOKEN to a random token of at least 16 characters")
        # The M1 coordinator cannot safely resume remote calls after a process crash.
        with sessions() as session:
            for run in session.scalars(select(Run).where(Run.status.in_(
                    ["pending", "running", "evaluating", "cancelling"]))):
                for trial in records(session, Trial, run_id=run.id):
                    for execution in records(session, Execution, trial_id=trial.id):
                        if execution.status == "invoking":
                            execution.status = "outcome_unknown"
                            execution.error = {"code": "COORDINATOR_INTERRUPTED"}
                        elif execution.status == "pending":
                            execution.status = "infrastructure_failed"
                            execution.error = {"code": "NOT_DISPATCHED_BEFORE_RESTART"}
                for batch in records(session, GradingBatch, run_id=run.id):
                    if batch.status != "completed":
                        batch.status = "failed"
                run.status = "failed"
            session.commit()
        yield
        engine.dispose()

    app = FastAPI(title="AgentEval", version=__version__, lifespan=lifespan,
                  description="M1: local coordinator; distributed recovery is not implemented.",
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
                "offset": offset, "limit": limit}

    def add(session, entity):
        session.add(entity)
        session.commit()
        return serialize(entity)

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @router.get("/openapi.json")
    def openapi():
        return app.openapi()

    @router.get("/capabilities")
    def capabilities():
        return {"version": __version__, "execution_mode": "single_process",
                "adapters": ["http", "python"], "evaluators": list(CAPABILITIES),
                "automatic_retries": False, "durable_queue": False, "project_concurrency_limits": False,
                "langgraph": False, "llm_judge": False,
                "pagination": "offset", "warning": "Use one API process; M2 recovery is not implemented"}

    @router.post("/projects", status_code=201)
    def create_project(body: Named, session=Depends(database)):
        return add(session, Project(**body.model_dump()))

    @router.get("/projects")
    def projects(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Project, offset, limit)

    @router.post("/agents", status_code=201)
    def create_agent(body: AgentCreate, session=Depends(database)):
        get(session, Project, body.project_id)
        return add(session, Agent(**body.model_dump()))

    @router.get("/agents")
    def agents(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Agent, offset, limit)

    @router.post("/agents/{agent_id}/versions", status_code=201)
    def create_agent_version(agent_id: str, body: AgentVersionCreate, session=Depends(database)):
        agent = get(session, Agent, agent_id)
        try:
            validate_agent_config(agent.adapter_type, body.configuration, settings)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "INVALID_AGENT_CONFIGURATION", "message": str(exc)})
        return add(session, AgentVersion(agent_id=agent_id, **body.model_dump(), content_hash=digest(body.model_dump())))

    @router.get("/agents/{agent_id}/versions")
    def agent_versions(agent_id: str, session=Depends(database)):
        get(session, Agent, agent_id)
        return {"items": [serialize(x) for x in records(session, AgentVersion, agent_id=agent_id)]}

    @router.post("/benchmarks", status_code=201)
    def create_benchmark(body: BenchmarkCreate, session=Depends(database)):
        get(session, Project, body.project_id)
        return add(session, Benchmark(**body.model_dump()))

    @router.get("/benchmarks")
    def benchmarks(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Benchmark, offset, limit)

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
    def benchmark_versions(benchmark_id: str, session=Depends(database)):
        get(session, Benchmark, benchmark_id)
        return {"items": [serialize(x) for x in records(session, BenchmarkVersion, benchmark_id=benchmark_id)]}

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
    def profiles(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, ProfileVersion, offset, limit)

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
        core = set(profile.definition["gate"]["core_case_keys"])
        if core - {c.case_key for c in cases}:
            raise HTTPException(422, detail={"code": "UNKNOWN_CORE_CASE"})
        errors = []
        for spec in specs:
            capability = CAPABILITIES[spec.id]
            if capability and capability not in version.capabilities:
                errors.append({"evaluator": spec.instance_id, "code": "CAPABILITY_MISSING", "capability": capability})
            for case in cases:
                try:
                    validate_case(spec, case.definition)
                except (KeyError, ValueError, TypeError, IndexError):
                    errors.append({"case_key": case.case_key, "evaluator": spec.instance_id, "code": "INVALID_ASSERTIONS"})
        if errors:
            raise HTTPException(422, detail={"code": "PREFLIGHT_FAILED", "errors": errors[:100]})
        run = Run(project_id=agent.project_id, **body.model_dump(), manifest={}, manifest_hash="")
        session.add(run)
        session.flush()
        run.manifest = snapshot(session, run)
        run.manifest_hash = digest(run.manifest)
        batch = GradingBatch(run_id=run.id, revision=0, grading_manifest={
            "parent_manifest_hash": run.manifest_hash, **profile.definition})
        session.add(batch)
        session.flush()
        for case in cases:
            for index in range(profile.definition["execution"]["repetitions"]):
                trial = Trial(run_id=run.id, case_id=case.id, repetition_index=index)
                session.add(trial)
                session.flush()
                session.add(Execution(trial_id=trial.id))
        if idempotency_key:
            session.add(RequestDedup(project_id=agent.project_id, key_hash=digest(idempotency_key),
                                     request_hash=digest(body.model_dump()), run_id=run.id))
        session.commit()
        tasks.add_task(runner.execute, run.id, batch.id)
        return {**serialize(run), "grading_batch_id": batch.id}

    @router.get("/evaluations")
    def runs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        return page(session, Run, offset, limit)

    @router.get("/evaluations/{run_id}")
    def run_detail(run_id: str, session=Depends(database)):
        run = get(session, Run, run_id)
        return {**serialize(run), "grading_batches": [serialize(x) for x in records(session, GradingBatch, run_id=run_id)]}

    @router.post("/evaluations/{run_id}/cancel")
    def cancel(run_id: str, session=Depends(database)):
        run = get(session, Run, run_id)
        if run.status not in ("completed", "failed", "cancelled"):
            run.cancel_requested = True
            run.status = "cancelling"
            session.commit()
        return {"status": run.status, "in_flight_policy": "finish_with_timeout; do not dispatch further calls"}

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
                             grading_manifest=batches[0].grading_manifest)
        session.add(batch)
        session.commit()
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
    def run_report(run_id: str, grading_batch_id: str | None = None, session=Depends(database)):
        return report(session, batch_for(session, run_id, grading_batch_id))

    @router.get("/evaluations/{run_id}/results")
    def results(run_id: str, grading_batch_id: str | None = None, offset: int = Query(0, ge=0),
                limit: int = Query(50, ge=1, le=100), session=Depends(database)):
        batch = batch_for(session, run_id, grading_batch_id)
        return {"grading_batch_id": batch.id,
                **page(session, EvaluationResult, offset, limit, batch_id=batch.id)}

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
                "format": "M1 observation channels; OTel spans not yet implemented"}

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
        return serialize(get(session, Comparison, comparison_id))

    app.include_router(router)
    return app


app = create_app()
