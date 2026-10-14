# syntax=docker/dockerfile:1
# __APP_NAME__ (Python 3.12 + FastAPI + hub_sdk). The hub builds this image from the package (`hub pack`/`hub deploy`).
# Non-root user, read-only root compatible (precompiled bytecode, temporary files in /scratch), HEALTHCHECK without curl.
FROM python:3.12-slim-bookworm AS builder
RUN pip install --no-cache-dir uv==0.12.23
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock* ./
COPY vendor vendor
COPY src src
RUN uv sync --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin hub \
    && mkdir -p /scratch && chown 10001:10001 /scratch
COPY --from=builder /opt/venv /opt/venv
ARG APP_VERSION=dev
LABEL hub.build.version=$APP_VERSION hub.stack=python
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080 \
    HUB_APP_VERSION_MARKER=$APP_VERSION TMPDIR=/scratch
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "__PKG__", "--health"]
ENTRYPOINT ["python", "-m", "__PKG__"]
