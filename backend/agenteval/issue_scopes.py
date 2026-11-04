"""Stable Agent identity, benchmark/case identity and explicit version applicability."""
from collections import defaultdict
from sqlalchemy import select

from agenteval.db import (AgentVersion, BenchmarkVersion, EvaluationResult, Execution, GradingBatch,
    IssueEvent, IssueScope, QualityIssue, QualityPolicy, Run, TestCase, Trial, TrialAssessment)
from agenteval.quality import case_identity, diagnose

INACTIVE = ("resolved", "ignored", "merged")


def ingestion_mode(session, project_id):
    policy = session.scalar(select(QualityPolicy).filter_by(project_id=project_id))
    return policy.ingestion_mode if policy else "automatic"


def collect_anomalies(session, batch, run, source="automatic"):
    existing = {i.execution_id: i for i in session.scalars(select(QualityIssue).filter_by(batch_id=batch.id))}
    grouped, attempts = defaultdict(list), defaultdict(list)
    for value in session.scalars(select(EvaluationResult).filter_by(batch_id=batch.id)):
        grouped[value.execution_id].append(value)
    for attempt in session.scalars(select(Execution).join(Trial, Trial.id == Execution.trial_id).where(Trial.run_id == run.id)):
        attempts[attempt.trial_id].append(attempt)
    av = session.get(AgentVersion, run.agent_version_id)
    bv = session.get(BenchmarkVersion, run.benchmark_version_id)
    imported = []
    for assessment in session.scalars(select(TrialAssessment).where(TrialAssessment.batch_id == batch.id, TrialAssessment.verdict != "passed")):
        issue = existing.get(assessment.selected_execution_id)
        if issue is None:
            trial = session.get(Trial, assessment.trial_id)
            case = session.get(TestCase, trial.case_id)
            execution = session.get(Execution, assessment.selected_execution_id)
            historical = [r for attempt in attempts[trial.id] if attempt.id != execution.id for r in grouped[attempt.id]
                if r.result["hard_constraint"] and r.result["passed"] is False]
            category, severity, evidence = diagnose(case, execution, grouped[execution.id], batch.grading_manifest["gate"]["core_case_keys"], historical)
            issue = QualityIssue(project_id=run.project_id, batch_id=batch.id, execution_id=execution.id, case_id=case.id,
                title=case.case_key, category=category, severity=severity, evidence=evidence, kind="anomaly")
            session.add(issue); session.flush()
            session.add(IssueScope(issue_id=issue.id, agent_id=av.agent_id, benchmark_id=bv.benchmark_id,
                case_hash=case_identity(case.definition), version_ids=[av.id]))
            session.add(IssueEvent(issue_id=issue.id, action="discovered", detail={"verdict": assessment.verdict,
                "source_batch_id": batch.id, "ingestion": source, "classification": "unconfirmed_anomaly"}))
        imported.append(issue)
    session.flush()
    return imported


def scope_for(session, issue_id):
    return session.scalar(select(IssueScope).filter_by(issue_id=issue_id))


def applicable_records(session, project_id, agent_id, version_id, benchmark_version_id):
    bv = session.get(BenchmarkVersion, benchmark_version_id)
    hashes = {case_identity(c.definition) for c in session.scalars(select(TestCase).filter_by(benchmark_version_id=benchmark_version_id))}
    rows = session.execute(select(QualityIssue, IssueScope).join(IssueScope, IssueScope.issue_id == QualityIssue.id).where(
        QualityIssue.project_id == project_id, IssueScope.agent_id == agent_id, IssueScope.benchmark_id == bv.benchmark_id))
    return [(issue, scope) for issue, scope in rows if version_id in scope.version_ids and scope.case_hash in hashes]
