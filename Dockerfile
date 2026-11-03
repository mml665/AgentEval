FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend ./backend
COPY alembic.ini pyproject.toml ./
COPY examples ./examples
ENV PYTHONPATH=/app/backend
RUN useradd --create-home agenteval && mkdir -p /app/data && chown -R agenteval:agenteval /app
USER agenteval
EXPOSE 8000
CMD ["sh", "-c", "python -m agenteval init-db && python -m agenteval serve --host 0.0.0.0"]

