# Suíte de conformidade do contrato de app (caixa-preta), em container para rodar
# dentro das redes internas do app (hub-app-<id> + hub-deps-<id>). Ver run.sh.
FROM python:3.12-slim-bookworm
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /suite/requirements.txt
RUN pip install -r /suite/requirements.txt
COPY conftest.py test_contract.py pytest.ini /suite/
COPY profiles /suite/profiles
WORKDIR /suite
USER 10001
ENTRYPOINT ["python", "-m", "pytest", "-v", "-rs"]
