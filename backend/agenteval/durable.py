"""Database-owned execution, transactional outbox, leases and fenced writes."""
import asyncio
from dataclasses import replace
from contextlib import contextmanager
import time
from uuid import uuid4

import httpx
from sqlalchemy import select, text

from agenteval.adapters import AgentFailure, invoke
from agenteval.contracts import EvaluatorSpec
from agenteval.db import (AuditEvent, EvaluationResult, Execution, GradingBatch, Project,
                         Run, TestCase, Trial, TrialAssessment, WorkItem)
from agenteval.evaluators import evaluate, verdict
from agenteval.tracing import invoke_traced, persist_trace, validated
from agenteval.db import TraceSpan

TERMINAL = {"completed", "agent_failed", "timed_out", "infrastructure_failed", "outcome_unknown", "cancelled"}


def enqueue(session, run, kind, target_id):
    item = WorkItem(project_id=run.project_id, run_id=run.id, kind=kind, target_id=target_id)
    session.add(item)
    return item


class Coordinator:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings

    @contextmanager
    def locked(self, project_id):
        with self.sessions() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.get(Project, project_id, with_for_update=True)
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def project(self, item_id):
        with self.sessions() as session:
            item = session.get(WorkItem, item_id)
            return item.project_id if item else None

    def fenced(self, item, owner, epoch):
        return item and item.status == "running" and item.owner == owner and item.epoch == epoch and item.lease_until > time.time()

    def claim(self, item_id):
        project = self.project(item_id)
        if not project:
            return None
        with self.locked(project) as session:
            item = session.get(WorkItem, item_id)
            if item.status not in ("pending", "published"):
                return None
            run = session.get(Run, item.run_id)
            if item.kind == "execute" and run.cancel_requested:
                session.get(Execution, item.target_id).status = "cancelled"
                item.status = "done"
                session.flush()
                self.maybe_grade(session, run)
                return None
            active = list(session.scalars(select(WorkItem).where(
                WorkItem.project_id == project, WorkItem.kind == item.kind,
                WorkItem.status.in_(["running", "blocked"]))))
            project_limit = self.settings.project_concurrency if item.kind == "execute" else 1
            run_limit = run.manifest["profile"]["execution"]["max_concurrency"]
            if len(active) >= project_limit or (item.kind == "execute" and sum(x.run_id == run.id for x in active) >= run_limit):
                return None
            item.owner, item.epoch = str(uuid4()), item.epoch + 1
            item.status, item.lease_until = "running", time.time() + self.settings.lease_seconds
            if item.kind == "execute":
                session.get(Execution, item.target_id).status = "invoking"
                if run.status in ("pending", "queued"):
                    run.status = "running"
            else:
                session.get(GradingBatch, item.target_id).status = "running"
            return item.owner, item.epoch, item.kind

    def renew(self, item_id, owner, epoch):
        with self.locked(self.project(item_id)) as session:
            item = session.get(WorkItem, item_id)
            if not self.fenced(item, owner, epoch):
                return False
            item.lease_until = time.time() + self.settings.lease_seconds
            return True

    async def process(self, item_id):
        claim = self.claim(item_id)
        if not claim:
            return
        owner, epoch, kind = claim
        lost = asyncio.Event()

        async def heartbeat():
            while True:
                await asyncio.sleep(self.settings.lease_seconds / 3)
                if not await asyncio.to_thread(self.renew, item_id, owner, epoch):
                    lost.set()
                    return

        heart = asyncio.create_task(heartbeat())
        try:
            if kind == "execute":
                await self.execute(item_id, owner, epoch, lost)
            else:
                await asyncio.to_thread(self.grade, item_id, owner, epoch)
        finally:
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)

    async def execute(self, item_id, owner, epoch, lost):
        with self.sessions() as session:
            item = session.get(WorkItem, item_id)
            run = session.get(Run, item.run_id)
            execution = session.get(Execution, item.target_id)
            trial = session.get(Trial, execution.trial_id)
            case = session.get(TestCase, trial.case_id)
            agent = run.manifest["agent"]
            timeout = run.manifest["profile"]["execution"]["timeout_seconds"]
            request = {"run_id": run.id, "trial_id": trial.id, "execution_id": execution.id,
                       "session_id": execution.id, "input": case.definition["input"],
                       "case_key": case.case_key, "trace_enabled": True}
        started = time.monotonic()
        response, error, status = None, None, "outcome_unknown"
        def on_span(row):
            with self.locked(item.project_id) as session:
                current = session.get(WorkItem, item_id)
                if not self.fenced(current, owner, epoch):
                    return
                observed = session.get(Execution, item.target_id)
                existing = list(session.scalars(select(TraceSpan).filter_by(execution_id=observed.id)))
                if existing and observed.trace_id != row.get("trace_id"):
                    return
                parent = row.get("parent_span_id")
                if parent is not None and parent not in {s.span_id for s in existing}:
                    return
                previous = next((s for s in existing if s.span_id == row.get("span_id")), None)
                if previous and previous.parent_span_id != parent:
                    return
                validated([row], row.get("trace_id"), parent)
                observed.trace_id, observed.trace_completeness = row["trace_id"], "partial"
                persist_trace(session, observed.id, [row])
        capture = {"on_span": on_span}
        environment = None
        definition = run.manifest["profile"].get("environment")
        if definition:
            from agenteval.environment import ENVIRONMENTS
            environment = ENVIRONMENTS[definition["adapter_id"]](self.settings.environment_root, execution.id)
            try:
                request["environment"] = await asyncio.to_thread(environment.prepare, case.definition["assertions"].get("environment_initial", {}))
            except Exception as exc:
                self.finish(item_id, owner, epoch, "infrastructure_failed", error={"code": "ENVIRONMENT_PREPARE_FAILED", "type": type(exc).__name__})
                return
        task = asyncio.create_task(invoke_traced(agent["adapter_type"], agent["configuration"], request, timeout, self.settings, capture))
        try:
            async with asyncio.timeout(timeout):
                while not task.done():
                    await asyncio.wait([task], timeout=.1)
                    with self.sessions() as session:
                        cancelled = session.get(Run, item.run_id).cancel_requested
                    if lost.is_set() or cancelled:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        status = "cancelled" if agent["adapter_type"] != "http" else "outcome_unknown"
                        error = {"code": "LEASE_LOST" if lost.is_set() else "CANCEL_REQUESTED",
                                 "remote_outcome": "unknown" if agent["adapter_type"] == "http" else "process_stopped"}
                        break
                else:
                    response = task.result()
                    status = "completed"
        except (TimeoutError, httpx.TimeoutException):
            status, error = "timed_out", {"code": "AGENT_TIMEOUT", "remote_outcome": "unknown"}
        except AgentFailure as exc:
            response = exc.response
            status, error = "agent_failed", {"code": "AGENT_PROTOCOL_OR_EXECUTION_ERROR"}
        except httpx.RequestError:
            status, error = "outcome_unknown", {"code": "HTTP_TRANSPORT_ERROR", "remote_outcome": "unknown"}
        except Exception:
            status, error = "outcome_unknown", {"code": "EXECUTOR_INTERRUPTED_AFTER_CLAIM"}
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if response is not None:
            response["trace_completeness"] = capture.get("completeness", "unavailable")
            response["trace_spans"] = capture.get("spans", [])
        environment_evidence = None
        if environment:
            from agenteval.environment import collect
            # Unknown remote effects retain their isolated state; reset only after confirmed completion.
            safe = status in ("completed", "agent_failed") and not lost.is_set()
            environment_evidence = await asyncio.to_thread(collect, environment, safe)
        self.finish(item_id, owner, epoch, status, response, error, (time.monotonic() - started) * 1000, capture, environment_evidence)

    def finish(self, item_id, owner, epoch, status, response=None, error=None, latency=None, trace_capture=None, environment_evidence=None):
        with self.locked(self.project(item_id)) as session:
            item = session.get(WorkItem, item_id)
            execution = session.get(Execution, item.target_id)
            if not self.fenced(item, owner, epoch):
                execution.late_result = {"epoch": epoch, "status": status, "response": response, "error": error, "environment_evidence": environment_evidence}
                session.add(AuditEvent(run_id=item.run_id, action="STALE_RESULT_REJECTED", detail={"work_id": item.id, "epoch": epoch}))
                return False
            execution.status, execution.response, execution.error = status, response, error
            execution.environment_evidence = environment_evidence
            if trace_capture and trace_capture.get("spans"):
                execution.trace_id = trace_capture["trace_id"]
                execution.trace_completeness = trace_capture.get("completeness", "partial")
                persist_trace(session, execution.id, trace_capture["spans"])
            execution.latency_ms = round(latency, 3) if latency is not None else None
            run = session.get(Run, item.run_id)
            unknown_effect = run.manifest["agent"].get("effects", "unknown") != "read_only"
            remote_unknown = error and error.get("remote_outcome") == "unknown"
            item.status = "blocked" if unknown_effect and (status == "outcome_unknown" or remote_unknown) else "done"
            session.flush()
            self.retry_or_grade(session, run, execution)
            return True

    def retry_or_grade(self, session, run, execution):
        policy = run.manifest["profile"]["execution"]
        if (not run.cancel_requested and run.manifest["agent"].get("effects") == "read_only"
                and execution.status in ("timed_out", "outcome_unknown", "infrastructure_failed")
                and execution.attempt_index < policy.get("max_retries", 0)):
            retry = Execution(trial_id=execution.trial_id, attempt_index=execution.attempt_index + 1)
            session.add(retry)
            session.flush()
            enqueue(session, run, "execute", retry.id)
            session.add(AuditEvent(run_id=run.id, action="READ_ONLY_RETRY", detail={"previous_execution": execution.id, "execution": retry.id}))
        else:
            self.maybe_grade(session, run)

    def maybe_grade(self, session, run):
        trials = list(session.scalars(select(Trial).filter_by(run_id=run.id)))
        for trial in trials:
            last = session.scalar(select(Execution).filter_by(trial_id=trial.id).order_by(Execution.attempt_index.desc()))
            if last.status not in TERMINAL:
                return
        batch = session.scalar(select(GradingBatch).filter_by(run_id=run.id, revision=0))
        exists = session.scalar(select(WorkItem).filter_by(kind="grade", target_id=batch.id))
        if not exists:
            enqueue(session, run, "grade", batch.id)
            run.status = "evaluating"

    def grade(self, item_id, owner, epoch):
        from agenteval.judge import judge
        with self.sessions() as session:
            item = session.get(WorkItem, item_id)
            batch = session.get(GradingBatch, item.target_id)
            specs = [EvaluatorSpec.model_validate(x) for x in batch.grading_manifest["evaluators"]]
            frozen_judge = batch.grading_manifest.get("judge", {})
            judge_settings = replace(self.settings, judge_endpoint=frozen_judge.get("endpoint", self.settings.judge_endpoint),
                                     judge_model=frozen_judge.get("model", self.settings.judge_model),
                                     judge_max_calls=frozen_judge.get("max_calls", self.settings.judge_max_calls))
            trials = list(session.scalars(select(Trial).filter_by(run_id=item.run_id)))
            work = []
            cases = {c.id: c for c in session.scalars(select(TestCase).where(TestCase.benchmark_version_id == session.get(Run, item.run_id).benchmark_version_id))}
            trial_map = {t.id: t for t in trials}
            executions = list(session.scalars(select(Execution).join(Trial, Execution.trial_id == Trial.id).where(Trial.run_id == item.run_id).order_by(Execution.attempt_index)))
            for execution in executions:
                work.append((execution, cases[trial_map[execution.trial_id].case_id].definition))
            scored = {(r.execution_id, r.instance_id) for r in session.scalars(select(EvaluationResult).filter_by(batch_id=batch.id))}
        for execution, case in work:
            for spec in specs:
                if (execution.id, spec.instance_id) in scored:
                    continue
                if spec.id == "llm_judge" and execution.status == "completed":
                    value = judge(spec, case, execution.response or {}, judge_settings, self, item_id, owner, epoch, batch.id, execution.id)
                else:
                    value = evaluate(spec, case, execution.response or {}, environment_evidence=execution.environment_evidence)
                with self.locked(item.project_id) as session:
                    current = session.get(WorkItem, item_id)
                    if not self.fenced(current, owner, epoch):
                        return
                    session.add(EvaluationResult(batch_id=batch.id, execution_id=execution.id, instance_id=spec.instance_id, result=value))
                scored.add((execution.id, spec.instance_id))
        with self.locked(item.project_id) as session:
            current = session.get(WorkItem, item_id)
            if not self.fenced(current, owner, epoch):
                return
            all_results = list(session.scalars(select(EvaluationResult).filter_by(batch_id=batch.id)))
            all_executions = list(session.scalars(select(Execution).join(Trial, Execution.trial_id == Trial.id).where(Trial.run_id == item.run_id).order_by(Execution.attempt_index)))
            from collections import defaultdict
            grouped_executions, grouped_results = defaultdict(list), defaultdict(list)
            for execution in all_executions:
                grouped_executions[execution.trial_id].append(execution)
            for score in all_results:
                grouped_results[score.execution_id].append(score.result)
            for trial in trials:
                executions = grouped_executions[trial.id]
                canonical = executions[-1]
                results = all_results
                selected = grouped_results[canonical.id]
                historical_hard = [r for e in executions[:-1] for r in grouped_results[e.id]
                                   if r["hard_constraint"] and r["passed"] is False]
                decision, reasons = verdict(canonical.status, selected + historical_hard)
                session.add(TrialAssessment(trial_id=trial.id, batch_id=batch.id, selected_execution_id=canonical.id, verdict=decision, reasons=reasons))
            session.get(GradingBatch, batch.id).status = "completed"
            current.status = "done"
            run = session.get(Run, item.run_id)
            if run.status not in ("completed", "cancelled", "failed"):
                run.status = "cancelled" if run.cancel_requested else "completed"

    def recover(self):
        with self.sessions() as session:
            expired = list(session.scalars(select(WorkItem.id).where(WorkItem.status == "running", WorkItem.lease_until < time.time())))
        for item_id in expired:
            with self.locked(self.project(item_id)) as session:
                item = session.get(WorkItem, item_id)
                if item.status != "running" or item.lease_until >= time.time():
                    continue
                item.epoch += 1
                item.owner = None
                run = session.get(Run, item.run_id)
                if item.kind == "grade":
                    item.status, item.next_delivery = "pending", 0
                else:
                    execution = session.get(Execution, item.target_id)
                    execution.status = "outcome_unknown"
                    execution.error = {"code": "WORKER_LEASE_EXPIRED", "remote_outcome": "unknown"}
                    item.status = "done" if run.manifest["agent"].get("effects") == "read_only" else "blocked"
                    session.flush()
                    self.retry_or_grade(session, run, execution)
                session.add(AuditEvent(run_id=run.id, action="LEASE_EXPIRED", detail={"work_id": item.id, "kind": item.kind}))

    def pending(self, run_id=None):
        with self.sessions() as session:
            query = select(WorkItem.id).where(WorkItem.status.in_(["pending", "published"]))
            if run_id:
                query = query.where(WorkItem.run_id == run_id)
            return list(session.scalars(query.order_by(WorkItem.created_at).limit(100)))

    async def drain(self, run_id=None):
        for _ in range(5000):
            ids = self.pending(run_id)
            if not ids:
                return
            await asyncio.gather(*(self.process(i) for i in ids))
            if set(self.pending(run_id)) == set(ids):
                # Capacity may be held by an unknown remote operation. Do not spin.
                return
            await asyncio.sleep(.02)

    def publish(self, celery_app):
        with self.sessions() as session:
            ids = list(session.scalars(select(WorkItem.id).where(
                WorkItem.status.in_(["pending", "published"]), WorkItem.next_delivery <= time.time()).limit(100)))
        for item_id in ids:
            try:
                celery_app.send_task("agenteval.work", args=[item_id], task_id=str(uuid4()), retry=False)
                with self.locked(self.project(item_id)) as session:
                    item = session.get(WorkItem, item_id)
                    if item.status in ("pending", "published"):
                        item.status, item.next_delivery = "published", time.time() + 5
                        item.delivery_count += 1
                        item.last_error = None
            except Exception:
                with self.locked(self.project(item_id)) as session:
                    item = session.get(WorkItem, item_id)
                    item.last_error, item.next_delivery = "BROKER_PUBLISH_FAILED", time.time() + 2


class LocalRunner:
    """Same database protocol as Celery; useful without a local Redis service."""
    def __init__(self, sessions, settings):
        self.coordinator = Coordinator(sessions, settings)

    async def execute(self, run_id, batch_id=None):
        await self.coordinator.drain(run_id)

    async def grade(self, run_id, batch_id):
        await self.coordinator.drain(run_id)
