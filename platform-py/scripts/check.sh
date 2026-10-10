#!/usr/bin/env bash
cd "$(dirname "$0")/.." && exec uv run hub-check "$@"
