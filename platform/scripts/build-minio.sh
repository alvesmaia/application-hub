#!/usr/bin/env bash
# Compila MinIO e mc do código-fonte oficial (ver infra/minio/Dockerfile) e imprime as
# variáveis para o compose usar essas imagens em vez dos digests do quay.io (que deixaram
# de ser públicos).
set -euo pipefail
cd "$(dirname "$0")/.."
MINIO_VERSION="${MINIO_VERSION:-RELEASE.2025-09-07T16-13-09Z}"
MC_VERSION="${MC_VERSION:-RELEASE.2025-08-13T08-35-41Z}"
docker build -f infra/minio/Dockerfile --target minio --build-arg "MINIO_VERSION=$MINIO_VERSION" --build-arg "MC_VERSION=$MC_VERSION" -t "hub-minio:$MINIO_VERSION" infra/minio
docker build -f infra/minio/Dockerfile --target mc --build-arg "MINIO_VERSION=$MINIO_VERSION" --build-arg "MC_VERSION=$MC_VERSION" -t "hub-mc:$MC_VERSION" infra/minio
echo "export HUB_MINIO_IMAGE=hub-minio:$MINIO_VERSION"
echo "export HUB_MC_IMAGE=hub-mc:$MC_VERSION"
