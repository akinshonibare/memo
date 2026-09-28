# One image, two Railway services: the API and the worker differ only in their start
# command (see railway.api.json / railway.worker.json). uv installs from the lockfile
# so the container runs exactly the tree CI tested (ADR 0009).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY evals ./evals
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

RUN useradd --create-home --uid 10001 memo
WORKDIR /app
COPY --from=build --chown=memo:memo /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER memo

EXPOSE 8000
# Railway injects PORT; the worker overrides this command entirely.
CMD ["sh", "-c", "uvicorn memo.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
