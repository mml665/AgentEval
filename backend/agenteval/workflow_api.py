"""Product workflows use the same execution, authorization and immutable resource contracts."""
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select
from agenteval.contracts import BenchmarkVersionCreate, EvaluatorSpec, RunCreate, digest
from agenteval.db import (Agent, AgentVersion, BaselineSetting, Benchmark, BenchmarkVersion, EvaluationDraft,
    GradingBatch, OnboardingReceipt, ProfileVersion, Project, RequestDedup, Run, TestCase)
from agenteval.security import actor_context, lock_security
from agenteval.workflow_contracts import CaseDocument, ReuseRun, SaveDraft, SetBaseline
from agenteval.workflow_validation import inspect_document, preview_guided
from agenteval.product_contracts import GuidedRun
from agenteval.preflight import prepare


def install(router, settings, sessions, database, get, serialize, create_run):
    @router.get("/workspace-versions/{project_id}")
    def available_versions(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), session=Depends(database)):
        from sqlalchemy import func
        get(session, Project, project_id)
        query = select(BenchmarkVersion, Benchmark.name).join(Benchmark).where(Benchmark.project_id == project_id)
        return {"items": [{**serialize(v), "benchmark_name": name} for v,name in session.execute(query.order_by(BenchmarkVersion.created_at.desc(), BenchmarkVersion.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    def own(session, project, av=None, pv=None):
        version = get(session, AgentVersion, av) if av else None
        profile = get(session, ProfileVersion, pv) if pv else None
        if version and get(session, Agent, version.agent_id).project_id != project or profile and profile.project_id != project:
            raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        return version, profile

    @router.post("/onboarding/preview")
    def guided_preview(body: GuidedRun, session=Depends(database)):
        if body.project_id: get(session, Project, body.project_id)
        try: return preview_guided(session, settings, body)
        except ValueError: raise HTTPException(422, detail={"code": "INVALID_GUIDED_CONFIGURATION"}) from None

    @router.post("/onboarding/reuse-preview")
    def reuse_preview(body: ReuseRun, session=Depends(database)):
        result = prepare(session, settings, RunCreate(**body.model_dump(include=set(RunCreate.model_fields))), get)
        if result["project_id"] != body.project_id: raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        return result

    @router.post("/onboarding/reuse", status_code=202)
    def reuse(body: ReuseRun, tasks: BackgroundTasks, session=Depends(database)):
        own(session, body.project_id, body.agent_version_id, body.profile_version_id)
        bv = get(session, BenchmarkVersion, body.benchmark_version_id)
        if get(session, Benchmark, bv.benchmark_id).project_id != body.project_id: raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        return create_run(RunCreate(**body.model_dump(include=set(RunCreate.model_fields))), tasks, "reuse:"+body.request_key, session)

    @router.get("/onboarding/requests/{request_key}")
    def request_result(request_key: str, request: Request, project_id: str | None = None, session=Depends(database)):
        if not 16 <= len(request_key) <= 128:
            raise HTTPException(422, detail={"code": "INVALID_REQUEST_KEY"})
        if project_id is None:
            principal = request.state.principal
            if not principal.admin:
                raise HTTPException(403, detail={"code": "PROJECT_SCOPE_REQUIRED"})
            row = session.scalar(select(Run).join(OnboardingReceipt).where(OnboardingReceipt.request_key == request_key))
            actor = (row.initiator or {}) if row else {}
            owned = row is not None and actor.get("kind") == principal.kind and actor.get("id") == principal.id
            # First-project recovery only needs a destination, without exposing any unscoped run data.
            return {"found": owned, "run": {"id": row.id, "project_id": row.project_id} if owned else None}
        get(session, Project, project_id)
        guided = session.scalar(select(OnboardingReceipt).join(Run, Run.id == OnboardingReceipt.run_id).where(OnboardingReceipt.request_key == request_key, Run.project_id == project_id))
        reused = session.scalar(select(RequestDedup).where(RequestDedup.project_id == project_id, RequestDedup.key_hash == digest("reuse:"+request_key)))
        row = get(session, Run, (guided or reused).run_id) if guided or reused else None
        if row and row.project_id != project_id: raise HTTPException(404)
        return {"found": bool(row), "run": serialize(row) if row else None, "retry": "Use the same request key and identical configuration if no committed result exists."}

    @router.get("/evaluations/{run_id}/reuse-config")
    def reuse_config(run_id: str, session=Depends(database)):
        run = get(session, Run, run_id)
        return {"project_id": run.project_id, "agent_version_id": run.agent_version_id,
            "benchmark_version_id": run.benchmark_version_id, "profile_version_id": run.profile_version_id,
            "source_run_id": run.id, "scope": "Resource references only; execution creates a new run and never edits the source."}

    def check_document(session, benchmark_id, body):
        benchmark = get(session, Benchmark, benchmark_id)
        av, pv = own(session, benchmark.project_id, body.agent_version_id, body.profile_version_id)
        specs = [EvaluatorSpec.model_validate(s) for s in pv.definition["evaluators"]] if pv else []
        cases, errors = inspect_document(body.document, specs, av.capabilities if av else None)
        return cases, errors

    @router.post("/benchmarks/{benchmark_id}/preview")
    def document_preview(benchmark_id: str, body: CaseDocument, session=Depends(database)):
        cases, errors = check_document(session, benchmark_id, body)
        return {"valid": not errors, "valid_case_count": len(cases) if not errors else 0, "parsed_case_count": len(cases), "errors": errors,
            "source_hash": digest(body.model_dump()), "cases": [{"case_key": c["case_key"], "input": c["input"], "assertions": c["assertions"], "tags": c["tags"]} for c in cases[:100]],
            "scope": "Display is redacted; publish the original submitted document, never this derived preview."}

    @router.post("/benchmarks/{benchmark_id}/publish", status_code=201)
    def document_publish(benchmark_id: str, body: CaseDocument, session=Depends(database)):
        cases, errors = check_document(session, benchmark_id, body)
        if errors: raise HTTPException(422, detail={"code": "CASE_DOCUMENT_INVALID", "errors": errors})
        validated = BenchmarkVersionCreate(version=body.version, cases=cases)
        cases = sorted([c.model_dump() for c in validated.cases], key=lambda c:c["case_key"])
        bv = BenchmarkVersion(benchmark_id=benchmark_id, version=body.version, case_count=len(cases), content_hash=digest({"schema_version":"0.1", "cases":cases}))
        session.add(bv); session.flush()
        for case in cases: session.add(TestCase(benchmark_version_id=bv.id, case_key=case["case_key"], definition=case))
        session.commit(); return serialize(bv)

    @router.get("/benchmark-versions/{version_id}/diff")
    def version_diff(version_id: str, other_version_id: str, session=Depends(database)):
        before, after = get(session, BenchmarkVersion, version_id), get(session, BenchmarkVersion, other_version_id)
        if before.benchmark_id != after.benchmark_id: raise HTTPException(422, detail={"code": "BENCHMARK_IDENTITY_MISMATCH"})
        def material(bv): return {c.case_key:c.definition for c in session.scalars(select(TestCase).where(TestCase.benchmark_version_id == bv.id))}
        left, right = material(before), material(after)
        changed = [k for k in sorted(left.keys() & right.keys()) if digest(left[k]) != digest(right[k])]
        return {"baseline_version_id": before.id, "candidate_version_id": after.id, "added": sorted(right.keys()-left.keys()), "removed": sorted(left.keys()-right.keys()),
            "modified": [{"case_key":k,"before":left[k],"after":right[k]} for k in changed], "unchanged": len(left.keys() & right.keys())-len(changed), "scope":"Differences computed from originals before display redaction."}

    @router.get("/quality/baselines/{project_id}")
    def baselines(project_id: str, agent_id: str | None = None, benchmark_version_id: str | None = None, session=Depends(database)):
        get(session, Project, project_id)
        query = select(BaselineSetting).where(BaselineSetting.project_id == project_id)
        if agent_id: query = query.where(BaselineSetting.agent_id == agent_id)
        if benchmark_version_id: query = query.where(BaselineSetting.benchmark_version_id == benchmark_version_id)
        return {"items": [{**serialize(r), "run": serialize(get(session, Run, get(session, GradingBatch, r.grading_batch_id).run_id))} for r in session.scalars(query.order_by(BaselineSetting.id).limit(1000))]}

    @router.post("/quality/baselines/{project_id}")
    def baseline(project_id: str, body: SetBaseline):
        with sessions() as session:
            lock_security(session)
            agent, bv, batch = get(session, Agent, body.agent_id), get(session, BenchmarkVersion, body.benchmark_version_id), get(session, GradingBatch, body.grading_batch_id)
            run = get(session, Run, batch.run_id)
            if agent.project_id != project_id or run.project_id != project_id or run.agent_version_id not in set(session.scalars(select(AgentVersion.id).where(AgentVersion.agent_id == agent.id))) or run.benchmark_version_id != bv.id:
                raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
            if batch.status != "completed" or run.status not in ("completed", "cancelled"): raise HTTPException(409, detail={"code": "RESULTS_NOT_FROZEN"})
            row = session.scalar(select(BaselineSetting).where(BaselineSetting.project_id == project_id, BaselineSetting.agent_id == agent.id, BaselineSetting.benchmark_version_id == bv.id))
            if (row.revision if row else 0) != body.expected_revision: raise HTTPException(409, detail={"code": "BASELINE_REVISION_CONFLICT"})
            if row: row.grading_batch_id, row.revision, row.actor = batch.id, row.revision+1, actor_context.get()
            else: row = BaselineSetting(project_id=project_id, agent_id=agent.id, benchmark_version_id=bv.id, grading_batch_id=batch.id, revision=1, actor=actor_context.get()); session.add(row)
            session.commit(); return serialize(row)

    def person(request):
        if request.state.principal.kind != "user": raise HTTPException(403, detail={"code": "PERSONAL_DRAFT_REQUIRED"})
        return request.state.principal.id

    @router.get("/onboarding/drafts/{project_id}")
    def load_draft(project_id: str, request: Request, session=Depends(database)):
        user_id = person(request)
        row = session.scalar(select(EvaluationDraft).where(EvaluationDraft.project_id == project_id, EvaluationDraft.user_id == user_id))
        request.state.draft_owner_authorized = True
        return {"revision": row.revision if row else 0, "payload": row.payload if row else None, "scope": "Private author-owned input, not a raw evidence retrieval route."}

    @router.post("/onboarding/drafts/{project_id}")
    def save_draft(project_id: str, body: SaveDraft, request: Request):
        user_id = person(request)
        with sessions() as session:
            lock_security(session); get(session, Project, project_id)
            payload = body.payload.model_dump()
            # Validate every stored resource reference even though the configuration can be incomplete.
            own(session, project_id, body.payload.agent_version_id, body.payload.profile_version_id)
            if body.payload.benchmark_version_id:
                bv = get(session, BenchmarkVersion, body.payload.benchmark_version_id)
                if get(session, Benchmark, bv.benchmark_id).project_id != project_id: raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
            row = session.scalar(select(EvaluationDraft).where(EvaluationDraft.project_id == project_id, EvaluationDraft.user_id == user_id))
            if (row.revision if row else 0) != body.expected_revision: raise HTTPException(409, detail={"code": "DRAFT_REVISION_CONFLICT"})
            if row: row.payload, row.revision = payload, row.revision+1
            else: row = EvaluationDraft(project_id=project_id, user_id=user_id, payload=payload, revision=1); session.add(row)
            session.commit(); return {"revision": row.revision, "project_id": project_id}
