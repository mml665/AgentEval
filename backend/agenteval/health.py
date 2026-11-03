"""Resilient service loops and explicit liveness/readiness separation."""
import asyncio
from dataclasses import dataclass, field
import os
import socket
import time

from sqlalchemy import func, select

from agenteval.db import ServiceHeartbeat, WorkItem


@dataclass
class LoopHealth:
    role: str
    instance_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    state: str = "starting"
    last_success: float = 0
    failures: int = 0
    error_code: str | None = None

    def success(self):
        self.state, self.last_success, self.failures, self.error_code = "healthy", time.time(), 0, None

    def fail(self, exc):
        self.state, self.failures, self.error_code = "degraded", self.failures + 1, type(exc).__name__


def persist(sessions, state):
    with sessions() as session:
        key = state.role + ":" + state.instance_id
        row = session.scalar(select(ServiceHeartbeat).filter_by(instance_id=key))
        if row is None:
            row = ServiceHeartbeat(instance_id=key, role=state.role)
            session.add(row)
        row.state, row.last_seen, row.last_success = state.state, time.time(), state.last_success
        row.failures, row.error_code = state.failures, state.error_code
        session.commit()


async def resilient_tick(state, operation, sessions):
    try:
        await operation()
        state.success()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.fail(exc)
    try:
        await asyncio.to_thread(persist, sessions, state)
    except Exception as exc:
        state.fail(exc)
    return min(10, 2 ** min(state.failures, 3)) if state.failures else 1


def status(sessions, settings, local_state, task_alive=True):
    current = time.time()
    with sessions() as session:
        session.execute(select(1))
        services = list(session.scalars(select(ServiceHeartbeat).order_by(ServiceHeartbeat.role)))
        counts = dict(session.execute(select(WorkItem.status, func.count()).group_by(WorkItem.status)).all())
        oldest = session.scalar(select(func.min(WorkItem.created_at)).where(WorkItem.status.in_(["pending", "published"])))
    rows = [{"instance_id": s.instance_id, "role": s.role,
             "state": s.state if current - s.last_seen < 15 else "stale",
             "last_seen": s.last_seen, "failures": s.failures, "error_code": s.error_code} for s in services]
    ready = task_alive and local_state.state == "healthy" and current - local_state.last_success < 15
    broker = "not_required"
    if settings.execution_mode == "celery":
        try:
            import redis
            connection = redis.Redis.from_url(settings.broker_url, socket_connect_timeout=.5, socket_timeout=.5)
            try:
                connection.ping()
                broker = "healthy"
            finally:
                connection.close()
        except Exception:
            broker, ready = "unavailable", False
        ready = ready and all(any(s["role"] == role and s["state"] == "healthy" for s in rows) for role in ("worker", "dispatcher"))
    return {"ready": bool(ready), "mode": settings.execution_mode, "database": "healthy", "broker": broker,
            "maintenance": {"state": local_state.state, "alive": task_alive, "failures": local_state.failures, "error_code": local_state.error_code},
            "services": rows, "queue_counts": counts,
            "oldest_queue_age_seconds": max(0, current - oldest.replace(tzinfo=__import__('datetime').timezone.utc).timestamp()) if oldest else 0}
