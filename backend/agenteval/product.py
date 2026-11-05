"""Quality workbench, guided evaluation, issue lifecycle and frozen release reviews."""
from copy import deepcopy
import json

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from agenteval.adapters import validate_agent_config
from agenteval import __version__
from agenteval.contracts import ProfileCreate, RunCreate, digest
from agenteval.db import (Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison, Execution,
    GradingBatch, IssueEvent, OnboardingReceipt, ProfileVersion, Project, QualityIssue,
    RegressionCollection, ReleaseDecision, Run, TestCase, TraceSpan, Trial, TrialAssessment)
from agenteval.evaluators import metadata, validate_case
from agenteval.product_contracts import (ConnectionCheck, CreateRelease, GuidedRun, ImportIssues, IssueRetest, IssueUpdate, StartRetest)
from agenteval.quality import case_identity, release_snapshot
from agenteval.service import report
from agenteval.issue_scopes import INACTIVE, collect_anomalies, applicable_records, scope_for
from agenteval.scope_api import resolve_scope, comparison_query


def install(router, settings, database, get, serialize, create_run, runner):
    def settled(session, batch_id):
        batch = get(session, GradingBatch, batch_id)
        run = get(session, Run, batch.run_id)
        if batch.status != "completed" or run.status not in ("completed", "cancelled"):
            raise HTTPException(409, detail={"code": "RESULTS_NOT_FROZEN"})
        return batch, run

    def issue_view(session, issue):
        from agenteval.evidence_summary import summarize
        case = get(session, TestCase, issue.case_id)
        batch = get(session, GradingBatch, issue.batch_id)
        execution = get(session, Execution, issue.execution_id)
        return {**serialize(issue), "case_key": case.case_key, "case_definition": case.definition,
                "run_id": batch.run_id, "issue_scope": serialize(scope_for(session, issue.id)),
                "fact_summary": summarize(issue.execution_id, batch.id, execution.trace_id, issue.evidence["facts"],
                    next((f["status"] for f in issue.evidence["facts"] if f["kind"] == "execution"), None), execution.response, case.definition, execution.error)}

    @router.get("/onboarding/options")
    def options():
        from agenteval.plugins import PLUGINS, GRAPH_PLUGINS
        from agenteval.environment import ENVIRONMENTS
        return {"python": list(PLUGINS), "langgraph": list(GRAPH_PLUGINS), "environments": list(ENVIRONMENTS)}

    @router.get("/quality/batches")
    def quality_batches(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), session=Depends(database)):
        query = select(GradingBatch).join(Run, Run.id == GradingBatch.run_id).where(Run.project_id == project_id, GradingBatch.status == "completed")
        return {"items": [serialize(b) for b in session.scalars(query.order_by(GradingBatch.created_at.desc(), GradingBatch.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.get("/quality/agent-versions")
    def quality_versions(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), session=Depends(database)):
        query = select(AgentVersion, Agent.name, Agent.adapter_type).join(Agent, Agent.id == AgentVersion.agent_id).where(Agent.project_id == project_id, Agent.adapter_type != "component")
        return {"items": [{**serialize(v), "agent_name": name, "adapter_type": adapter} for v, name, adapter in session.execute(query.order_by(AgentVersion.created_at.desc(), AgentVersion.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.get("/quality/runs")
    def quality_runs(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), session=Depends(database)):
        query = (select(Run, Agent.name, AgentVersion.version, Benchmark.name, Agent.id, Benchmark.id)
            .join(AgentVersion, AgentVersion.id == Run.agent_version_id).join(Agent, Agent.id == AgentVersion.agent_id)
            .join(BenchmarkVersion, BenchmarkVersion.id == Run.benchmark_version_id).join(Benchmark, Benchmark.id == BenchmarkVersion.benchmark_id)
            .where(Run.project_id == project_id, Run.status.in_(["completed", "cancelled"])))
        return {"items": [{**serialize(run), "agent_label": f"{name} · v{version}", "agent_id": agent_id, "benchmark_id": benchmark_id, "benchmark_label": benchmark} for run, name, version, benchmark, agent_id, benchmark_id in session.execute(
            query.order_by(Run.created_at.desc(), Run.id).offset(offset).limit(limit))],
            "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.get("/quality/comparisons")
    def quality_comparisons(project_id: str, agent_id: str | None = None, candidate_version_id: str | None = None, baseline_version_id: str | None = None, benchmark_version_id: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), session=Depends(database)):
        from agenteval.comparison_context import views
        query = select(Comparison).join(Run, Run.id == Comparison.report["candidate"]["run_id"].as_string()).where(Run.project_id == project_id)
        if any((agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)):
            scope = resolve_scope(session, get, project_id, agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)
            query = comparison_query(scope)
        return {"items": views(session, list(session.scalars(query.order_by(Comparison.created_at.desc(), Comparison.id).offset(offset).limit(limit))), serialize),
            "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.post("/onboarding/check")
    async def connection_check(body: ConnectionCheck, session=Depends(database)):
        try:
            validate_agent_config(body.adapter_type, body.configuration, settings)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "INVALID_AGENT_CONFIGURATION", "message": str(exc)})
        if body.adapter_type != "http":
            return {"status": "registered", "capabilities_verified": False, "message": "部署插件已注册；观测能力将在首次执行中核验。"}
        from agenteval.credentials import resolve, CredentialError
        try:
            headers = resolve(session, settings, body.project_id, body.configuration)
        except CredentialError as exc:
            raise HTTPException(422, detail={"code": exc.code}) from None
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
                reply = await client.head(body.configuration["endpoint"], headers=headers)
            return {"status": "reachable", "http_status": reply.status_code, "capabilities_verified": False,
                    "checks": {"address": "verified", "network": "reachable", "authentication": "rejected" if reply.status_code in (401,403) else "not_verified", "protocol": "not_verified", "observations": "not_verified"},
                    "message": "HEAD 鉴权被拒，请核对凭据及接口权限；业务 POST 协议尚未验证。" if reply.status_code in (401,403) else "HEAD 方法不支持（405）；不能据此判断业务 POST，请选择测试用例明确验证。" if reply.status_code == 405 else "服务已响应 HEAD 探测；未发送业务执行 POST，执行协议与观测能力待首次评测核验。"}
        except httpx.RequestError:
            raise HTTPException(422, detail={"code": "CONNECTION_FAILED", "message": "HEAD 连通性检查失败，请检查服务与网络允许清单。"})

    @router.post("/onboarding/evaluations", status_code=202)
    def guided_run(body: GuidedRun, tasks: BackgroundTasks, session=Depends(database)):
        receipt = session.scalar(select(OnboardingReceipt).filter_by(request_key=body.request_key))
        request_hash = digest(body.model_dump(exclude={"request_key"}))
        if receipt:
            if receipt.request_hash != request_hash:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return serialize(get(session, Run, receipt.run_id))
        try:
            validate_agent_config(body.adapter_type, body.agent.configuration, settings)
            preset = {"answer": [{"instance_id": "answer", "id": "exact_match"}],
                "tools": [{"instance_id": "tools", "id": "tool_call"}],
                "trajectory": [{"instance_id": "trajectory", "id": "trajectory"}],
                "retrieval": [{"instance_id": metric, "id": "rag", "config": {"metric": metric, "k": 5}} for metric in ("recall", "mrr", "ndcg")],
                "state": [{"instance_id": "state", "id": "state_assertion"}]}
            profile = ProfileCreate(project_id=body.project_id or "pending", name="向导评分方案", version="1",
                evaluators=[rule for goal in body.goals for rule in preset[goal]], execution=body.execution, gate=body.gate,
                environment={"adapter_id": body.environment_id} if body.environment_id else None)
            errors = []
            for spec in profile.evaluators:
                if set(metadata(spec)["required_capabilities"]) - set(body.agent.capabilities):
                    errors.append({"evaluator": spec.instance_id, "code": "CAPABILITY_MISSING"})
                for case in body.benchmark.cases:
                    try:
                        validate_case(spec, case.model_dump())
                    except (KeyError, ValueError, TypeError, IndexError):
                        errors.append({"case_key": case.case_key, "evaluator": spec.instance_id, "code": "INVALID_ASSERTIONS"})
            if errors:
                raise HTTPException(422, detail={"code": "PREFLIGHT_FAILED", "errors": errors})
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "INVALID_GUIDED_CONFIGURATION", "message": str(exc)})
        project = get(session, Project, body.project_id) if body.project_id else Project(name=body.project_name, description="通过三步评测向导创建")
        session.add(project); session.flush()
        from agenteval.credentials import validate_reference
        validate_reference(session, settings, project.id, body.agent.configuration)
        agent = Agent(project_id=project.id, name=body.agent_name, adapter_type=body.adapter_type, description="向导接入")
        session.add(agent); session.flush()
        av = AgentVersion(agent_id=agent.id, **body.agent.model_dump(), content_hash=digest(body.agent.model_dump()))
        session.add(av)
        bm = Benchmark(project_id=project.id, name=f"{body.agent_name} · 评测用例", description="向导导入的固定测试集")
        session.add(bm); session.flush()
        cases = sorted([c.model_dump() for c in body.benchmark.cases], key=lambda c: c["case_key"])
        bv = BenchmarkVersion(benchmark_id=bm.id, version=body.benchmark.version, case_count=len(cases), content_hash=digest({"schema_version": "0.1", "cases": cases}))
        session.add(bv); session.flush()
        for case in cases:
            session.add(TestCase(benchmark_version_id=bv.id, case_key=case["case_key"], definition=case))
        definition = profile.model_dump(exclude={"project_id", "name", "description", "version"})
        pv = ProfileVersion(project_id=project.id, name=f"向导方案-{agent.id[:8]}", version="1", definition=definition, content_hash=digest(definition))
        session.add(pv); session.flush()
        # Same transaction and same preflight/Runner as advanced configuration.
        session.info["pending_onboarding_receipt"] = {"request_key": body.request_key, "request_hash": request_hash}
        run = create_run(RunCreate(agent_version_id=av.id, benchmark_version_id=bv.id, profile_version_id=pv.id), tasks, None, session)
        return run

    @router.post("/quality/issues/import", status_code=201)
    def import_issues(body: ImportIssues, session=Depends(database)):
        batch, run = settled(session, body.grading_batch_id)
        if batch.grading_manifest.get("engine_version") != __version__:
            raise HTTPException(409, detail={"code": "ENGINE_REASSESSMENT_REQUIRED"})
        project_id = run.project_id
        session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            imported = [serialize(i) for i in collect_anomalies(locked, get(locked, GradingBatch, batch.id), get(locked, Run, run.id), "manual")]
        return {"items": imported, "source_batch_id": batch.id}

    @router.get("/quality/issues")
    def issues(project_id: str, status: str | None = None, agent_id: str | None = None, candidate_version_id: str | None = None, baseline_version_id: str | None = None, benchmark_version_id: str | None = None, assignee_user_id: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), session=Depends(database)):
        get(session, Project, project_id)
        query = select(QualityIssue).where(QualityIssue.project_id == project_id)
        if any((agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)):
            scope = resolve_scope(session, get, project_id, agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)
            ids = [i.id for i, _ in applicable_records(session, project_id, scope["agent_id"], scope["candidate_version_id"], scope["benchmark_version_id"])]
            query = query.where(QualityIssue.id.in_(ids))
        if assignee_user_id: query = query.where(QualityIssue.assignee_user_id == assignee_user_id)
        grouped = list(session.execute(query.with_only_columns(QualityIssue.kind, QualityIssue.status, func.count()).group_by(QualityIssue.kind, QualityIssue.status)))
        triage = {"pending": sum(n for kind,state,n in grouped if kind == "anomaly" and state == "open"),
            "repair": sum(n for kind,state,n in grouped if kind == "issue" and state in ("open", "confirmed", "fix_in_progress")),
            "retest": sum(n for kind,state,n in grouped if kind == "issue" and state == "ready_for_retest")}
        if status:
            query = query.where(QualityIssue.status == status)
        return {"triage_counts": triage, "items": [issue_view(session, i) for i in session.scalars(query.order_by(QualityIssue.created_at.desc(), QualityIssue.id).offset(offset).limit(limit))],
                "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.get("/quality/issues/{issue_id}")
    def issue_detail(issue_id: str, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id)
        spans = list(session.scalars(select(TraceSpan).filter_by(execution_id=issue.execution_id)))
        return {**issue_view(session, issue), "trace": [s.record for s in spans],
            "events": [serialize(e) for e in session.scalars(select(IssueEvent).filter_by(issue_id=issue_id).order_by(IssueEvent.created_at, IssueEvent.id))]}

    @router.post("/quality/issues/{issue_id}/update")
    def update_issue(issue_id: str, body: IssueUpdate, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id)
        project_id = issue.project_id
        session.rollback()
        transitions = {"open": {"confirmed"}, "confirmed": {"fix_in_progress"}, "fix_in_progress": {"ready_for_retest"},
                       "ready_for_retest": {"fix_in_progress"}, "resolved": {"open"}, "ignored": {"open"}, "merged": set()}
        with runner.coordinator.locked(project_id) as locked:
            issue = get(locked, QualityIssue, issue_id)
            if issue.revision != body.expected_revision:
                raise HTTPException(409, detail={"code": "ISSUE_REVISION_CONFLICT"})
            if body.status != issue.status and body.status not in transitions[issue.status]:
                raise HTTPException(409, detail={"code": "INVALID_ISSUE_TRANSITION"})
            before = issue.status
            if body.status == "confirmed":
                issue.kind = "issue"
            from agenteval.security import assign_member
            assign_member(locked, issue, body.assignee_user_id, body.owner)
            issue.status, issue.confirmed_cause = body.status, body.confirmed_cause
            issue.revision += 1
            locked.add(IssueEvent(issue_id=issue.id, action="developer_update", detail={"before": before, **body.model_dump()}))
            locked.flush(); value = serialize(issue)
        return value

    @router.post("/quality/issues/{issue_id}/retest")
    def retest_issue(issue_id: str, body: IssueRetest, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id)
        project_id = issue.project_id
        session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            issue = get(locked, QualityIssue, issue_id)
            if issue.revision != body.expected_revision or issue.status != "ready_for_retest":
                raise HTTPException(409, detail={"code": "ISSUE_NOT_READY_OR_STALE"})
            batch, run = settled(locked, body.grading_batch_id)
            origin = get(locked, GradingBatch, issue.batch_id)
            if run.project_id != project_id or batch.id == origin.id:
                raise HTTPException(422, detail={"code": "INVALID_RETEST_BATCH"})
            if any(batch.grading_manifest.get(k) != origin.grading_manifest.get(k) for k in ("evaluators", "execution", "environment", "judge", "engine_version")):
                raise HTTPException(422, detail={"code": "RETEST_POLICY_MISMATCH"})
            issue_scope = scope_for(locked, issue.id)
            if get(locked, AgentVersion, run.agent_version_id).agent_id != issue_scope.agent_id or get(locked, BenchmarkVersion, run.benchmark_version_id).benchmark_id != issue_scope.benchmark_id:
                raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
            identity = issue.evidence["case_hash"]
            matching = [c.id for c in locked.scalars(select(TestCase).filter_by(benchmark_version_id=run.benchmark_version_id)) if case_identity(c.definition) == identity]
            if not matching:
                raise HTTPException(422, detail={"code": "RETEST_CASE_MISMATCH"})
            rows = list(locked.scalars(select(TrialAssessment).join(Trial, Trial.id == TrialAssessment.trial_id).where(
                TrialAssessment.batch_id == batch.id, Trial.case_id.in_(matching))))
            expected = len(matching) * batch.grading_manifest["execution"]["repetitions"]
            fixed = len(rows) == expected and all(a.verdict == "passed" for a in rows)
            issue.status = "resolved" if fixed else "fix_in_progress"
            issue.retest_batch_id = batch.id; issue.revision += 1
            locked.add(IssueEvent(issue_id=issue.id, action="retest", detail={"batch_id": batch.id, "resolved": fixed, "note": body.note, "verdicts": [a.verdict for a in rows]}))
            locked.flush(); value = serialize(issue)
        return value

    @router.post("/quality/issues/{issue_id}/evaluations", status_code=202)
    def start_retest(issue_id: str, body: StartRetest, tasks: BackgroundTasks, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id)
        if issue.status != "ready_for_retest":
            raise HTTPException(409, detail={"code": "ISSUE_NOT_READY_OR_STALE"})
        issue_scope = scope_for(session, issue.id)
        if get(session, AgentVersion, body.agent_version_id).agent_id != issue_scope.agent_id:
            raise HTTPException(422, detail={"code": "EVALUATION_SCOPE_MISMATCH"})
        origin = get(session, Run, get(session, GradingBatch, issue.batch_id).run_id)
        run = create_run(RunCreate(agent_version_id=body.agent_version_id, benchmark_version_id=origin.benchmark_version_id,
            profile_version_id=origin.profile_version_id), tasks, body.request_key, session)
        session.add(IssueEvent(issue_id=issue.id, action="retest_started", detail={"run_id": run["id"], "agent_version_id": body.agent_version_id}))
        session.commit()
        return run

    @router.post("/quality/issues/{issue_id}/regression-case", status_code=201)
    def promote_case(issue_id: str, session=Depends(database)):
        issue = get(session, QualityIssue, issue_id)
        project_id = issue.project_id
        session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            issue = get(locked, QualityIssue, issue_id)
            if issue.regression_version_id:
                return serialize(get(locked, BenchmarkVersion, issue.regression_version_id))
            collection = locked.scalar(select(RegressionCollection).filter_by(project_id=project_id))
            if not collection:
                bm = Benchmark(project_id=project_id, name="长期回归集", description="从已跟踪 Bad Case 沉淀，所有版本不可变")
                locked.add(bm); locked.flush()
                collection = RegressionCollection(project_id=project_id, benchmark_id=bm.id)
                locked.add(collection); locked.flush()
            previous = list(locked.scalars(select(BenchmarkVersion).filter_by(benchmark_id=collection.benchmark_id).order_by(BenchmarkVersion.created_at.desc(), BenchmarkVersion.id)))
            cases = [deepcopy(c.definition) for c in locked.scalars(select(TestCase).filter_by(benchmark_version_id=previous[0].id))] if previous else []
            source = deepcopy(get(locked, TestCase, issue.case_id).definition)
            if previous and any(case_identity(c) == case_identity(source) for c in cases):
                issue.regression_version_id = previous[0].id; issue.revision += 1
                locked.add(IssueEvent(issue_id=issue.id, action="promoted_to_regression", detail={"benchmark_version_id": previous[0].id, "reused_existing_case": True}))
                return serialize(previous[0])
            if not any(case_identity(c) == case_identity(source) for c in cases):
                if source["case_key"] in {c["case_key"] for c in cases}:
                    source["case_key"] += "--" + case_identity(source)[:8]
                source["tags"] = list(dict.fromkeys(source.get("tags", []) + ["regression", f"issue:{issue.id}"]))
                cases.append(source)
            cases.sort(key=lambda c: c["case_key"])
            next_version = 1 + max((int(v.version) for v in previous if v.version.isdecimal()), default=0)
            version = BenchmarkVersion(benchmark_id=collection.benchmark_id, version=str(next_version), case_count=len(cases), content_hash=digest({"schema_version": "0.1", "cases": cases}))
            locked.add(version); locked.flush()
            for c in cases:
                locked.add(TestCase(benchmark_version_id=version.id, case_key=c["case_key"], definition=c))
            issue.regression_version_id = version.id; issue.revision += 1
            locked.add(IssueEvent(issue_id=issue.id, action="promoted_to_regression", detail={"benchmark_version_id": version.id}))
            value = serialize(version)
        return value

    @router.post("/release-decisions", status_code=201)
    def create_release(body: CreateRelease, session=Depends(database)):
        comparison = get(session, Comparison, body.comparison_id)
        candidate_batch, run = settled(session, comparison.report["candidate_batch_id"])
        project_id = run.project_id
        session.rollback()
        with runner.coordinator.locked(project_id) as locked:
            comparison = get(locked, Comparison, body.comparison_id)
            candidate_batch, run = settled(locked, comparison.report["candidate_batch_id"])
            candidate_version = get(locked, AgentVersion, run.agent_version_id)
            applicable = applicable_records(locked, project_id, candidate_version.agent_id, candidate_version.id, run.benchmark_version_id)
            blockers = [{"id": i.id, "revision": i.revision, "title": i.title, "status": i.status, "issue_scope": jsonable_encoder(serialize(scope))}
                for i, scope in applicable if i.kind == "issue" and i.severity == "blocking" and i.status not in INACTIVE]
            newer = locked.scalar(select(func.count()).select_from(GradingBatch).where(GradingBatch.run_id == run.id, GradingBatch.revision > candidate_batch.revision)) > 0
            baseline_batch = get(locked, GradingBatch, comparison.report["baseline_batch_id"])
            current = all(b.grading_manifest.get("engine_version") == __version__ for b in (baseline_batch, candidate_batch))
            baseline_run = get(locked, Run, baseline_batch.run_id)
            same_agent = get(locked, AgentVersion, baseline_run.agent_version_id).agent_id == candidate_version.agent_id
            value = release_snapshot(comparison, blockers, newer, body.note, current, same_agent, baseline_run.benchmark_version_id == run.benchmark_version_id)
            value["evaluation_scope"] = {"agent_id": candidate_version.agent_id, "candidate_version_id": candidate_version.id,
                "baseline_version_id": baseline_run.agent_version_id, "benchmark_version_id": run.benchmark_version_id,
                "benchmark_id": get(locked, BenchmarkVersion, run.benchmark_version_id).benchmark_id}
            versions = {}
            for side in ("baseline", "candidate"):
                side_run = get(locked, Run, comparison.report[side]["run_id"])
                av = get(locked, AgentVersion, side_run.agent_version_id)
                versions[side] = {"agent_name": get(locked, Agent, av.agent_id).name, "version": av.version, "version_id": av.id, "agent_id": av.agent_id}
            value["versions"] = versions
            decision = ReleaseDecision(project_id=project_id, comparison_id=comparison.id, status=value["status"], snapshot=value)
            locked.add(decision); locked.flush(); data = serialize(decision)
        return data

    @router.get("/release-decisions")
    def release_list(project_id: str, agent_id: str | None = None, candidate_version_id: str | None = None, baseline_version_id: str | None = None, benchmark_version_id: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), session=Depends(database)):
        query = select(ReleaseDecision).where(ReleaseDecision.project_id == project_id)
        if any((agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)):
            scope = resolve_scope(session, get, project_id, agent_id, candidate_version_id, baseline_version_id, benchmark_version_id)
            query = comparison_query(scope, decision=True).where(ReleaseDecision.project_id == project_id)
        return {"items": [serialize(d) for d in session.scalars(query.order_by(ReleaseDecision.created_at.desc(), ReleaseDecision.id).offset(offset).limit(limit))],
                "total": session.scalar(select(func.count()).select_from(query.subquery()))}

    @router.get("/release-decisions/{decision_id}")
    def release_detail(decision_id: str, session=Depends(database)):
        return serialize(get(session, ReleaseDecision, decision_id))

    @router.get("/release-decisions/{decision_id}/export")
    def export_release(decision_id: str, format: str = Query("json", pattern="^(json|html)$"), session=Depends(database)):
        from agenteval.exports import redact
        decision = get(session, ReleaseDecision, decision_id)
        value = redact(serialize(decision))
        from agenteval.protection import view_context
        if view_context.get(): value["view"] = view_context.get().metadata()
        value = {**value, "content_hash": digest(value)}
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        if format == "html":
            from agenteval.release_exports import render_release
            payload = render_release(value)
        return Response(payload, media_type="application/json" if format == "json" else "text/html", headers={
            "Content-Disposition": f'attachment; filename="release-{decision.id}.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    from agenteval.scope_api import install as install_scopes
    install_scopes(router, database, get, serialize, runner, issue_view)
