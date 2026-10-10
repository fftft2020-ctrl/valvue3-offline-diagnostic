#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
action="${1:-help}"
if (($#)); then shift; fi
compose=(docker compose)
case "$action" in
  setup)
    if [[ "$(id -u)" == 0 ]]; then echo 'Run setup as your ordinary Linux user, not root.' >&2; exit 1; fi
    docker compose version >/dev/null
    if [[ ! -e .env ]]; then
      cp .env.example .env
      sed -i "s/^LOCAL_UID=.*/LOCAL_UID=$(id -u)/;s/^LOCAL_GID=.*/LOCAL_GID=$(id -g)/" .env
    fi
    # Defaults only: if using custom paths, create those with your own permissions.
    mkdir -p 离线诊断/projects .runtime/models .runtime/codex
    chmod 700 .runtime .runtime/codex
    echo 'Prepared .env and local folders. Existing .env was preserved.'
    ;;
  build) "${compose[@]}" build "$@";;
  login)
    # Start the shared namespace only when OCR was explicitly selected.
    if "${compose[@]}" config --services | grep -qx ocr; then
      "${compose[@]}" up -d ocr
    fi
    "${compose[@]}" run --rm --no-deps diagnostic login "$@"
    ;;
  doctor|shell|agent|init-project|report|ocr-smoke|isolation-smoke)
    "${compose[@]}" run --rm diagnostic "$action" "$@"
    ;;
  stop) "${compose[@]}" down "$@";;
  help)
    echo 'Usage: bash docker/run.sh {setup|build|login|doctor|shell|agent|init-project|report|ocr-smoke|isolation-smoke|stop} [args]'
    echo 'Read docs/DOCKER.md before using the isolation profile or WPS image.'
    ;;
  *) echo "Unknown action: $action" >&2; exit 2;;
esac
