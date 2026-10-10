#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" == 0 ]]; then
  echo 'Run diagnostic as a non-root user; set LOCAL_UID/LOCAL_GID to your own UID/GID.' >&2
  exit 1
fi
umask 077
skill="${SKILL_DIR:-/workspace/.agents/skills/valvue3-offline-diagnostic}"
command_name="${1:-shell}"
if (($#)); then shift; fi
case "$command_name" in
  shell) command=(bash "$@");;
  login) command=(codex -c 'cli_auth_credentials_store="file"' login --device-auth "$@");;
  agent) command=(codex -c 'cli_auth_credentials_store="file"' "$@");;
  doctor) command=(python /usr/local/lib/valvue/doctor.py "$@");;
  init-project) command=(python "$skill/scripts/init_offline_project.py" "$@");;
  report) command=(python "$skill/scripts/run_offline_report.py" "$@");;
  ocr-smoke) command=(python /usr/local/lib/valvue/ocr_smoke.py "$@");;
  isolation-smoke) command=(python /usr/local/lib/valvue/isolation_smoke.py "$@");;
  *) command=("$command_name" "$@");;
esac
if [[ "$command_name" == login ]]; then
  mkdir -p "$CODEX_HOME"
  test -w "$CODEX_HOME" || { echo 'Codex directory is not writable; check LOCAL_UID/LOCAL_GID.' >&2; exit 1; }
fi
if command -v wps >/dev/null && [[ "${VALVUE_START_XVFB:-1}" == 1 ]]; then
  # Private display, UNIX socket only. No unauthenticated TCP desktop listener.
  Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp > /tmp/valvue-xvfb.log 2>&1 &
  display_pid=$!
  trap 'kill "$display_pid" 2>/dev/null || true' EXIT
  ready=0
  for ((attempt=0; attempt<50; attempt++)); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$display_pid" 2>/dev/null || break
    sleep 0.1
  done
  if [[ "$ready" != 1 ]]; then echo 'Xvfb failed; see /tmp/valvue-xvfb.log.' >&2; exit 1; fi
  "${command[@]}" &
  command_pid=$!
  trap 'kill "$command_pid" "$display_pid" 2>/dev/null || true' TERM INT
  status=0
  wait "$command_pid" || status=$?
  exit "$status"
else
  # DISPLAY must not appear usable in the base image without a WPS display.
  unset DISPLAY
  exec "${command[@]}"
fi
