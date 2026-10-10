# syntax=docker/dockerfile:1
# hub-fornecedores: app "Rede de Fornecedores" (Python 3.12 + FastAPI + hub-sdk-python).
# Build (a partir de platform/):  docker build -f demo-apps-py/fornecedores/Dockerfile -t hub-fornecedores .
# Contexto enxuto: Dockerfile.dockerignore (só sdk-python, o app e os assets
# compartilhados do mockup).
#
# - multi-stage: dependências instaladas num venv no builder (sem compiladores na final);
# - usuário 10001 sem shell; raiz somente leitura compatível (bytecode pré-compilado,
#   PYTHONDONTWRITEBYTECODE, temporários em /scratch via TMPDIR);
# - HEALTHCHECK sem curl: `python -m fornecedores --health` (GET /fornecedores no próprio app);
# - cadeia de suprimentos: imagem base fixada por digest; TODAS as dependências (inclusive
#   as transitivas e o setuptools do build) vêm de demo-apps-py/fornecedores/requirements.lock,
#   instaladas com --require-hashes; os dois pacotes locais entram com --no-deps
#   --no-build-isolation --no-index (nada é resolvido nem baixado fora do lock).
#   Atualizar: "Atualizar dependências Python" no platform/README.md.
ARG PY_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
FROM ${PY_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY demo-apps-py/fornecedores/requirements.lock /src/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r /src/requirements.lock
COPY sdk-python /src/sdk-python
COPY demo-apps-py/fornecedores /src/fornecedores
RUN /opt/venv/bin/pip install --no-deps --no-build-isolation --no-index /src/sdk-python /src/fornecedores \
    && /opt/venv/bin/pip check \
    && /opt/venv/bin/python -m compileall -q /opt/venv

FROM ${PY_IMAGE}
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin hub \
    && mkdir -p /scratch && chown 10001:10001 /scratch
COPY --from=builder /opt/venv /opt/venv
COPY demo-apps/assets/demo.css /opt/fornecedores/shared-assets/
ARG APP_VERSION=dev
LABEL hub.build.version=$APP_VERSION hub.stack=python
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FORNECEDORES_HOST=0.0.0.0 \
    FORNECEDORES_SHARED_ASSETS=/opt/fornecedores/shared-assets \
    HUB_APP_VERSION_MARKER=$APP_VERSION \
    PORT=8080 \
    TMPDIR=/scratch
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "fornecedores", "--health"]
ENTRYPOINT ["python", "-m", "fornecedores"]
