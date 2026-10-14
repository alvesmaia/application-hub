# Imagem selada do hub: código + assets dentro da imagem, sem montagens de código e sem --reload.
# Build (contexto = raiz do repositório): docker build -f platform-py/Dockerfile -t app-hub-web .
# mc (MinIO Client) só para «Provisionar armazenamento»; imagem local `app-hub-mc` (fonte: infra/minio/Dockerfile, alvo mc).
ARG MC_IMAGE=app-hub-mc:0.10.0
FROM ${MC_IMAGE} AS mc

# Kit do desenvolvedor (F22): wheels da hub-cli e do hub-sdk servidos em /dev/cli/simple/.
FROM python:3.12-slim AS cli
RUN pip install --no-cache-dir uv==0.12.23
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
RUN sed -i 's/\r$//' platform-py/scripts/build-cli.sh && bash platform-py/scripts/build-cli.sh

FROM python:3.12-slim
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    HUB_PLATFORM_DIR=/platform HUB_ASSET_DIRS=/platform/assets/shell:/platform/assets/web HUB_APPS_FILE=/platform/config/apps.json
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app
COPY platform-py/pyproject.toml ./
RUN uv sync --no-install-project --no-dev
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
