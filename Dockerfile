FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1 TZ=UTC
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}
# /app/data (journal, state, caches) is a volume so restarts resume where they left off
CMD ["uv", "run", "--no-dev", "python", "-m", "saraf.engine", "run"]
