# Microsoft Graph/Entra ID simulator (sharepoint-sim profile). Tests/demo only.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir "starlette>=0.40" "uvicorn>=0.30" "cryptography>=45"
WORKDIR /app
COPY graphsim /app/graphsim
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=4s --retries=5 CMD ["python", "-m", "graphsim", "--health"]
CMD ["python", "-m", "graphsim"]
