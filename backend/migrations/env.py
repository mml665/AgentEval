from alembic import context
from agenteval.config import Settings
from agenteval.db import Base, make_database

config = context.config
url = config.attributes.get("database_url") or Settings().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = make_database(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

