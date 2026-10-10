# syntax=docker/dockerfile:1
# MinIO (servidor) e mc (cliente) compilados do código-fonte oficial (github.com/minio),
# nas MESMAS versões das imagens fixadas no docker-compose.yml. Desde o fim de 2025 as
# imagens públicas do MinIO (quay.io/minio, docker.io/minio) não são mais baixáveis sem
# credencial: numa máquina nova (e no CI) o compose não sobe com os digests fixados.
#
#   bash scripts/build-minio.sh            # gera hub-minio:<versão> e hub-mc:<versão>
#   export HUB_MINIO_IMAGE=hub-minio:RELEASE.2025-09-07T16-13-09Z
#   export HUB_MC_IMAGE=hub-mc:RELEASE.2025-08-13T08-35-41Z
#   docker compose up -d
FROM golang:1.24-alpine AS build
RUN apk add --no-cache git
ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
RUN --mount=type=cache,id=hub-go-mod,target=/go/pkg/mod \
    --mount=type=cache,id=hub-go-build,target=/root/.cache/go-build \
    git clone --depth 1 --branch "$MINIO_VERSION" https://github.com/minio/minio /src/minio \
    && cd /src/minio && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/minio .
RUN --mount=type=cache,id=hub-go-mod,target=/go/pkg/mod \
    --mount=type=cache,id=hub-go-build,target=/root/.cache/go-build \
    git clone --depth 1 --branch "$MC_VERSION" https://github.com/minio/mc /src/mc \
    && cd /src/mc && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/mc .

# Cliente: entrypoint `mc` (hub-objstore-init usa /bin/sh; as tarefas de backup passam só
# os argumentos do mc).
FROM alpine:3.20 AS mc
COPY --from=build /out/mc /usr/bin/mc
ENTRYPOINT ["mc"]

# Servidor: `minio server /data`; traz o mc para o healthcheck (`mc ready local`).
FROM alpine:3.20 AS minio
COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /out/mc /usr/bin/mc
EXPOSE 9000
ENTRYPOINT ["minio"]
