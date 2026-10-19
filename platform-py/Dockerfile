# Sealed hub image: code and assets baked into the image, no code mounts and no --reload.
# Build (context = repository root): docker build -f platform-py/Dockerfile -t app-hub-web .
# mc (MinIO Client) only for «Provisionar armazenamento» (storage provisioning); local image `app-hub-mc` (source: infra/minio/Dockerfile, mc target).
ARG MC_IMAGE=app-hub-mc:0.10.0
# Corporate network (scripts/install.sh): image mirror prefix and an extra CA appended to the system bundle.
ARG IMAGE_PREFIX=
FROM ${MC_IMAGE} AS mc

# Developer kit (F22): hub-cli and hub-sdk wheels served at /dev/cli/simple/.
FROM ${IMAGE_PREFIX}python:3.12-slim AS cli
ARG EXTRA_CA_PEM=""
ARG PIP_INDEX_URL
ARG UV_DEFAULT_INDEX
RUN if [ -n "$EXTRA_CA_PEM" ]; then printf '%s\n' "$EXTRA_CA_PEM" >> /etc/ssl/certs/ca-certificates.crt; fi
RUN env ${EXTRA_CA_PEM:+PIP_CERT=/etc/ssl/certs/ca-certificates.crt} pip install --no-cache-dir uv==0.12.23
WORKDIR /src
COPY platform-py/pyproject.toml platform-py/pyproject.toml
COPY platform-py/hub platform-py/hub
COPY platform-py/cli platform-py/cli
COPY platform-py/scripts/build-cli.sh platform-py/scripts/build-cli.sh
COPY platform/sdk-python platform/sdk-python
COPY platform/assets/shell platform/assets/shell
COPY platform/assets/web platform/assets/web
COPY platform/demo-apps/assets/demo.css platform/demo-apps/assets/demo.css
COPY platform/conformance/conftest.py platform/conformance/test_contract.py platform/conformance/pytest.ini platform/conformance/
RUN sed -i 's/\r$//' platform-py/scripts/build-cli.sh && env ${EXTRA_CA_PEM:+SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt} bash platform-py/scripts/build-cli.sh

FROM ${IMAGE_PREFIX}python:3.12-slim
ARG EXTRA_CA_PEM=""
ARG PIP_INDEX_URL
ARG UV_DEFAULT_INDEX
RUN if [ -n "$EXTRA_CA_PEM" ]; then printf '%s\n' "$EXTRA_CA_PEM" >> /etc/ssl/certs/ca-certificates.crt; fi
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    HUB_PLATFORM_DIR=/platform HUB_ASSET_DIRS=/platform/assets/shell:/platform/assets/web HUB_APPS_FILE=/platform/config/apps.json
RUN env ${EXTRA_CA_PEM:+PIP_CERT=/etc/ssl/certs/ca-certificates.crt} pip install --no-cache-dir uv==0.12.23
WORKDIR /app
COPY platform-py/pyproject.toml ./
RUN env ${EXTRA_CA_PEM:+SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt} uv sync --no-install-project --no-dev
COPY platform-py/hub /app/hub
COPY platform/assets/shell /platform/assets/shell
COPY platform/assets/i18n /platform/assets/i18n
COPY platform/assets/web /platform/assets/web
COPY platform/config/apps.json platform/config/apps.compose.json platform/config/app-env.compose.json /platform/config/
COPY --from=cli /src/platform-py/dist/cli /platform/dist/cli
COPY platform/docs/CONTRATO-APP.md /platform/docs/CONTRATO-APP.md
COPY platform/docs/dev /platform/docs/dev
COPY platform-py/cli/hub_cli/templates/python-fastapi/AGENTS.md /platform/docs/AGENTS-app.md
EXPOSE 8090
CMD ["uvicorn", "hub.main:app", "--host", "0.0.0.0", "--port", "8090"]
