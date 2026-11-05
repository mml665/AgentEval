FROM node:22-slim AS web
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY benchmarks /build/benchmarks
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend ./backend
COPY alembic.ini pyproject.toml ./
COPY examples ./examples
COPY --from=web /build/frontend/dist ./frontend/dist
ENV PYTHONPATH=/app/backend
RUN useradd --create-home agenteval && mkdir -p /app/data/environments && chown -R agenteval:agenteval /app
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/mml665/AgentEval" \
      org.opencontainers.image.revision=$SOURCE_REVISION
USER agenteval
EXPOSE 8000
CMD ["sh", "-c", "python -m agenteval init-db && python -m agenteval serve --host 0.0.0.0"]

