"""Workspace batch reads, trace inspection, report export and component runs."""
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from agenteval.contracts import ComponentRunCreate, RunCreate, digest
from agenteval.db import (Agent, AgentVersion, Benchmark, BenchmarkVersion, Comparison, EvaluationResult,
                         Execution, GradingBatch, JudgeBudget, JudgeCall, ProfileVersion, Project, Run, TestCase, TraceSpan, Trial, TrialAssessment)
from agenteval.calibration import CalibrationRequest, analyze
from agenteval.service import report


def install(router, settings, sessions, database, get, serialize, create_run, runner):
    @router.post("/evaluations/{run_id}/calibration")
    def calibration(run_id: str, body: CalibrationRequest, session=Depends(database)):
        run = get(session, Run, run_id)
        batch = get(session, GradingBatch, body.grading_batch_id)
        if batch.run_id != run_id:
            raise HTTPException(404)
        if batch.status != "completed":
            raise HTTPException(409, detail={"code": "GRADING_NOT_COMPLETE"})
        spec = next((e for e in batch.grading_manifest["evaluators"] if e["instance_id"] == body.instance_id), None)
        if not spec or spec["id"] != "llm_judge":
            raise HTTPException(422, detail={"code": "CALIBRATION_REQUIRES_JUDGE_INSTANCE"})
        cases = {c.id: c for c in session.scalars(select(TestCase).filter_by(benchmark_version_id=run.benchmark_version_id))}
        if {x.case_key for x in body.labels} - {c.case_key for c in cases.values()}:
            raise HTTPException(422, detail={"code": "UNKNOWN_CALIBRATION_CASE"})
        trials = {t.id: t for t in session.scalars(select(Trial).filter_by(run_id=run_id))}
        selected = {a.selected_execution_id: cases[trials[a.trial_id].case_id].case_key for a in session.scalars(select(TrialAssessment).filter_by(batch_id=batch.id))}
        ratings = [{"case_key": selected[r.execution_id], **r.result} for r in session.scalars(select(EvaluationResult).where(
            EvaluationResult.batch_id == batch.id, EvaluationResult.instance_id == body.instance_id)) if r.execution_id in selected]
        report = analyze([x.model_dump() for x in body.labels], ratings, planned_repetitions=batch.grading_manifest["execution"]["repetitions"])
        return {**report, "run_id": run.id, "grading_batch_id": batch.id, "instance_id": body.instance_id,
                "manifest_hash": run.manifest_hash, "judge": batch.grading_manifest.get("judge"),
                "annotation_source": body.annotation_source, "labelled_cases": len(body.labels),
                "labels": [x.model_dump() for x in body.labels],
                "benchmark_cases": len(cases)}

    @router.get("/workspace/{project_id}")
    def workspace(project_id: str, limit: int = Query(25, ge=1, le=100),
        agents_offset: int = Query(0, ge=0), benchmarks_offset: int = Query(0, ge=0),
        profiles_offset: int = Query(0, ge=0), runs_offset: int = Query(0, ge=0),
        comparisons_offset: int = Query(0, ge=0), session=Depends(database)):
        get(session, Project, project_id)
        result, totals, offsets = {}, {}, {}
        definitions = [("agents", Agent, agents_offset), ("benchmarks", Benchmark, benchmarks_offset),
                       ("profiles", ProfileVersion, profiles_offset), ("runs", Run, runs_offset), ("comparisons", Comparison, comparisons_offset)]
        for name, model, offset in definitions:
            query = select(model)
            if model is Comparison:
                query = query.join(Run, Run.id == Comparison.report["baseline"]["run_id"].as_string()).where(Run.project_id == project_id)
            else:
                query = query.where(model.project_id == project_id)
            totals[name] = session.scalar(select(func.count()).select_from(query.subquery()))
            offsets[name] = offset
            result[name] = [serialize(row) for row in session.scalars(query.order_by(model.created_at.desc(), model.id).offset(offset).limit(limit))]
        agent_ids = [a["id"] for a in result["agents"]]
        benchmark_ids = [b["id"] for b in result["benchmarks"]]
        av_ids = [r["agent_version_id"] for r in result["runs"]]
        bv_ids = [r["benchmark_version_id"] for r in result["runs"]]
        avs = list(session.scalars(select(AgentVersion).where(or_(AgentVersion.agent_id.in_(agent_ids), AgentVersion.id.in_(av_ids))).order_by(AgentVersion.created_at.desc()).limit(500)))
        bvs = list(session.scalars(select(BenchmarkVersion).where(or_(BenchmarkVersion.benchmark_id.in_(benchmark_ids), BenchmarkVersion.id.in_(bv_ids))).order_by(BenchmarkVersion.created_at.desc()).limit(500)))
        result["agentVersions"], result["benchmarkVersions"] = [serialize(v) for v in avs], [serialize(v) for v in bvs]
        result["agent_names"] = {a.id: a.name for a in session.scalars(select(Agent).where(Agent.id.in_([v.agent_id for v in avs])))}
        result["benchmark_names"] = {b.id: b.name for b in session.scalars(select(Benchmark).where(Benchmark.id.in_([v.benchmark_id for v in bvs])))}
        totals["agentVersions"] = session.scalar(select(func.count()).select_from(AgentVersion).join(Agent).where(Agent.project_id == project_id))
        totals["active_runs"] = session.scalar(select(func.count()).select_from(Run).where(Run.project_id == project_id, Run.status.not_in(["completed", "failed", "cancelled"])))
        result.update(totals=totals, offsets=offsets, limit=limit, versions_truncated=len(avs) >= 500 or len(bvs) >= 500)
        return result

    @router.get("/evaluations/{run_id}/judge-budget")
    def judge_budget(run_id: str, session=Depends(database)):
        get(session, Run, run_id)
        budget = session.scalar(select(JudgeBudget).filter_by(run_id=run_id))
        used = session.scalar(select(func.count()).select_from(JudgeCall).join(GradingBatch, JudgeCall.batch_id == GradingBatch.id).where(GradingBatch.run_id == run_id))
        return {"max_calls": budget.max_calls if budget else None, "reserved_calls": used,
                "remaining_calls": max(0, budget.max_calls - used) if budget else None,
                "policy_source": budget.policy_source if budget else "legacy_not_frozen"}

    @router.get("/executions/{execution_id}/trace")
    def execution_trace(execution_id: str, session=Depends(database)):
        execution = get(session, Execution, execution_id)
        spans = [row.record for row in session.scalars(select(TraceSpan).filter_by(execution_id=execution_id))]
        spans.sort(key=lambda s: (s["start_ns"], s["span_id"]))
        return {"execution_id": execution.id, "trace_id": execution.trace_id, "spans": spans,
                "completeness": execution.trace_completeness,
                "late_result_available": execution.late_result is not None}

    @router.get("/evaluations/{run_id}/export")
    def export(run_id: str, grading_batch_id: str, format: str = Query("json", pattern="^(json|csv|html)$"), session=Depends(database)):
        run = get(session, Run, run_id)
        batch = get(session, GradingBatch, grading_batch_id)
        if batch.run_id != run_id:
            raise HTTPException(404)
        if batch.status != "completed" or run.status not in ("completed", "cancelled", "failed"):
            raise HTTPException(409, detail={"code": "RESULTS_NOT_FROZEN"})
        from agenteval.exports import render
        bundle = {"schema_version": "1.0", "report": report(session, batch), "manifest": run.manifest,
                  "manifest_hash": run.manifest_hash, "grading_manifest": batch.grading_manifest,
                  "results": [serialize(r) for r in session.scalars(select(EvaluationResult).filter_by(batch_id=batch.id).order_by(EvaluationResult.execution_id, EvaluationResult.instance_id))]}
        data, media = render(bundle, format)
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="agenteval-{run.id}-{batch.id}.{format}"',
                                                       "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/regressions/{comparison_id}/export")
    def export_comparison(comparison_id: str, session=Depends(database)):
        import json
        from agenteval.exports import redact
        comparison = get(session, Comparison, comparison_id)
        return Response(json.dumps(redact(comparison.report), ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="regression-{comparison.id}.json"', "Cache-Control": "no-store"})

    @router.get("/components")
    def components():
        from agenteval.components import COMPONENTS
        return {"items": [{"id": key, **{k: v for k, v in value.items() if k != "invoke"}} for key, value in COMPONENTS.items()]}

    @router.post("/component-evaluations", status_code=202)
    def create_component_run(body: ComponentRunCreate, tasks: BackgroundTasks, session=Depends(database)):
        from jsonschema import Draft202012Validator
        from agenteval.components import COMPONENTS
        component = COMPONENTS.get(body.component_id)
        if component is None:
            raise HTTPException(422, detail={"code": "UNKNOWN_COMPONENT"})
        get(session, Project, body.project_id)
        benchmark = get(session, BenchmarkVersion, body.benchmark_version_id)
        if get(session, Benchmark, benchmark.benchmark_id).project_id != body.project_id:
            raise HTTPException(422, detail={"code": "PROJECT_MISMATCH"})
        for case in session.scalars(select(TestCase).filter_by(benchmark_version_id=benchmark.id)):
            if not Draft202012Validator(component["input_schema"]).is_valid(case.definition["input"]):
                raise HTTPException(422, detail={"code": "COMPONENT_INPUT_INVALID", "case_key": case.case_key})
        # Serialize registry materialization so simultaneous requests reuse one version.
        session.rollback()
        with runner.coordinator.locked(body.project_id) as locked:
            agent = locked.scalar(select(Agent).where(Agent.project_id == body.project_id, Agent.adapter_type == "component", Agent.name == body.component_id))
            if agent is None:
                agent = Agent(project_id=body.project_id, name=body.component_id, adapter_type="component", description=component["name"])
                locked.add(agent); locked.flush()
            version = locked.scalar(select(AgentVersion).filter_by(agent_id=agent.id, version="1.0"))
            if version is None:
                config = {"plugin_id": body.component_id}
                version = AgentVersion(agent_id=agent.id, version="1.0", configuration=config, effects="read_only",
                                       capabilities=component["capabilities"], content_hash=digest(config))
                locked.add(version); locked.flush()
            version_id = version.id
        return create_run(RunCreate(agent_version_id=version_id, benchmark_version_id=body.benchmark_version_id,
                                   profile_version_id=body.profile_version_id), tasks, None, session)
