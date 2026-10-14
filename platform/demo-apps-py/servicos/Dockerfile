# syntax=docker/dockerfile:1
# hub-servicos: app "Central de Serviços" (Python 3.12 + FastAPI + hub-sdk-python).
# Build (from platform/):  docker build -f demo-apps-py/servicos/Dockerfile -t hub-servicos .
# Lean context: Dockerfile.dockerignore (only sdk-python, the app and the assets
# shared with the mockup).
#
# - multi-stage: dependencies installed in a venv in the builder (no compilers in the final image);
# - user 10001 with no shell; read-only root compatible (precompiled bytecode,
#   PYTHONDONTWRITEBYTECODE, temp files in /scratch via TMPDIR);
# - HEALTHCHECK without curl: `python -m servicos --health` (GET /servicos on the app itself);
# - supply chain: base image pinned by digest; ALL dependencies (including
#   transitive ones and the build's setuptools) come from demo-apps-py/servicos/requirements.lock,
#   installed with --require-hashes; the two local packages are installed with --no-deps
#   --no-build-isolation --no-index (nothing is resolved or downloaded outside the lock).
#   To update: "Atualizar dependências Python" in platform/README.md.
ARG PY_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
FROM ${PY_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY demo-apps-py/servicos/requirements.lock /src/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r /src/requirements.lock
COPY sdk-python /src/sdk-python
COPY demo-apps-py/servicos /src/servicos
RUN /opt/venv/bin/pip install --no-deps --no-build-isolation --no-index /src/sdk-python /src/servicos \
    && /opt/venv/bin/pip check \
    && /opt/venv/bin/python -m compileall -q /opt/venv

FROM ${PY_IMAGE}
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin hub \
    && mkdir -p /scratch && chown 10001:10001 /scratch
COPY --from=builder /opt/venv /opt/venv
COPY demo-apps/assets/demo.css /opt/servicos/shared-assets/
ARG APP_VERSION=dev
LABEL hub.build.version=$APP_VERSION hub.stack=python
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICOS_HOST=0.0.0.0 \
    SERVICOS_SHARED_ASSETS=/opt/servicos/shared-assets \
    HUB_APP_VERSION_MARKER=$APP_VERSION \
    PORT=8080 \
    TMPDIR=/scratch
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "servicos", "--health"]
ENTRYPOINT ["python", "-m", "servicos"]
