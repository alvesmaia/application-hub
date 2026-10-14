# syntax=docker/dockerfile:1
# MinIO (server) and mc (client) built from the official source (github.com/minio),
# at the SAME versions as the images pinned in docker-compose.yml. Since the end of 2025 the
# public MinIO images (quay.io/minio, docker.io/minio) can no longer be pulled without
# credentials: on a new machine (and in CI) compose does not start with the pinned digests.
#
#   bash scripts/build-minio.sh            # generates hub-minio:<version> and hub-mc:<version>
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

# Client: `mc` entrypoint (hub-objstore-init uses /bin/sh; backup jobs pass only
# the mc arguments).
FROM alpine:3.20 AS mc
COPY --from=build /out/mc /usr/bin/mc
ENTRYPOINT ["mc"]

# Server: `minio server /data`; brings mc in for the healthcheck (`mc ready local`).
FROM alpine:3.20 AS minio
COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /out/mc /usr/bin/mc
EXPOSE 9000
ENTRYPOINT ["minio"]
