import asyncio
import time
import threading

from celery import Celery

from agenteval.config import Settings
from agenteval.db import make_database
from agenteval.durable import Coordinator
from agenteval.health import LoopHealth, persist
from celery.signals import worker_ready, worker_shutdown

settings = Settings()
app = Celery("agenteval", broker=settings.broker_url)
app.conf.update(task_serializer="json", accept_content=["json"], task_ignore_result=True,
                task_acks_late=True, task_reject_on_worker_lost=True,
                worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
                broker_connection_timeout=2, broker_transport_options={"visibility_timeout": 600, "socket_timeout": 2})


@app.task(name="agenteval.work")
def work(item_id):
    engine, sessions = make_database(Settings().database_url)
    try:
        asyncio.run(Coordinator(sessions, Settings()).process(item_id))
    finally:
        engine.dispose()


def dispatch_forever():
    engine, sessions = make_database(settings.database_url)
    coordinator = Coordinator(sessions, settings)
    state = LoopHealth("dispatcher")
    try:
        while True:
            try:
                coordinator.recover()
                coordinator.publish(app)
                state.success()
            except Exception as exc:
                state.fail(exc)
            try:
                persist(sessions, state)
            except Exception as exc:
                state.fail(exc)
            time.sleep(min(10, 2 ** min(state.failures, 3)) if state.failures else .5)
    finally:
        engine.dispose()


stop_heartbeat = threading.Event()


@worker_ready.connect
def start_worker_health(**kwargs):
    def heartbeat():
        engine, sessions = make_database(settings.database_url)
        state = LoopHealth("worker")
        try:
            while not stop_heartbeat.is_set():
                try:
                    state.success()
                    persist(sessions, state)
                except Exception as exc:
                    state.fail(exc)
                stop_heartbeat.wait(2)
        finally:
            state.state = "stopped"
            try:
                persist(sessions, state)
            except Exception:
                pass
            engine.dispose()
    threading.Thread(target=heartbeat, daemon=True).start()


@worker_shutdown.connect
def stop_worker_health(**kwargs):
    stop_heartbeat.set()
