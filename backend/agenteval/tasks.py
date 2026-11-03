import asyncio
import time

from celery import Celery

from agenteval.config import Settings
from agenteval.db import make_database
from agenteval.durable import Coordinator

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
    try:
        while True:
            coordinator.recover()
            coordinator.publish(app)
            time.sleep(.5)
    finally:
        engine.dispose()
