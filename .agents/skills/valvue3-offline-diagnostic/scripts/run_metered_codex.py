#!/usr/bin/env python3
"""Run one Codex call and append durable timing/token telemetry."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offline_engine.distribution import runtime_identity


TOKEN_PATTERN = re.compile(r"tokens used\s*\n\s*([0-9,]+)", re.I)
CALL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def parse_tokens(output: str) -> int | None:
    matches = TOKEN_PATTERN.findall(output)
    return int(matches[-1].replace(",", "")) if matches else None


def _telemetry_paths(project_root: Path) -> tuple[Path, Path, Path]:
    root = project_root.resolve()
    if not (root / "project.toml").is_file():
        raise ValueError(f"not a diagnostic project: {root}")
    directory = root / "work/model-calls"
    directory.mkdir(parents=True, exist_ok=True)
    return directory, directory / "ledger.jsonl", root / "work/workflow-summary.json"


def _read_records(handle: Any) -> list[dict[str, Any]]:
    handle.seek(0)
    records = []
    for line in handle:
        if line.strip():
            records.append(json.loads(line))
    return records


def _build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [item for item in records if item["event_type"] == "model_call"]
    outcomes: dict[str, dict[str, Any]] = {}
    for item in records:
        if item["event_type"] == "outcome":
            outcomes[item["call_id"]] = item
    stages: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        grouped[call["stage"]].append(call)
    for stage, items in sorted(grouped.items()):
        final_outcomes = [
            outcomes.get(item["call_id"], {}).get("outcome", "pending_review")
            for item in items
        ]
        rejection_reasons = Counter(
            outcomes[item["call_id"]].get("reason") or "unspecified"
            for item in items
            if outcomes.get(item["call_id"], {}).get("outcome") == "rejected"
        )
        tokens = [item["tokens_used"] for item in items]
        stages[stage] = {
            "started_at": min(item["started_at"] for item in items),
            "ended_at": max(item["ended_at"] for item in items),
            "calls": len(items),
            "valve_count": len(
                {item["valve_tag"] for item in items if item.get("valve_tag")}
            ),
            "tokens_used": sum(item or 0 for item in tokens),
            "unmetered_calls": sum(item is None for item in tokens),
            "elapsed_seconds": round(
                sum(float(item["elapsed_seconds"]) for item in items), 3
            ),
            "completed": sum(item["status"] == "completed" for item in items),
            "failed": sum(item["status"] == "failed" for item in items),
            "accepted": final_outcomes.count("accepted"),
            "rejected": final_outcomes.count("rejected"),
            "pending_review": final_outcomes.count("pending_review"),
            "retry_reasons": dict(sorted(rejection_reasons.items())),
        }
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "calls": len(calls),
        "tokens_used": sum(item.get("tokens_used") or 0 for item in calls),
        "unmetered_calls": sum(
            item.get("tokens_used") is None for item in calls
        ),
        "elapsed_seconds": round(
            sum(float(item["elapsed_seconds"]) for item in calls), 3
        ),
        "stages": stages,
    }


def _append_record(project_root: Path, record: dict[str, Any]) -> Path:
    directory, ledger, summary_path = _telemetry_paths(project_root)
    with ledger.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        records = _read_records(handle)
        if record["event_type"] == "model_call" and any(
            item["event_type"] == "model_call"
            and item["call_id"] == record["call_id"]
            for item in records
        ):
            raise ValueError(f"duplicate model call id: {record['call_id']}")
        if record["event_type"] == "outcome" and not any(
            item["event_type"] == "model_call"
            and item["call_id"] == record["call_id"]
            for item in records
        ):
            raise ValueError(f"unknown model call id: {record['call_id']}")
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        records.append(record)
        summary = _build_summary(records)
        temporary = directory / f"workflow-summary.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(summary_path)
    return summary_path


def _require_file(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _codex_executable(explicit: Path | None = None) -> str:
    if explicit is not None:
        return str(_require_file(explicit, "Codex executable"))
    primary = shutil.which("codex")
    if not primary:
        raise RuntimeError("codex executable is unavailable")
    host_version = os.environ.get("CODEX_VERSION")
    if host_version and Path(primary).is_file():
        # PATH may put an older npm CLI ahead of the desktop runtime. Prefer an
        # already installed binary matching this host, without replacing either.
        candidates = dict.fromkeys((Path(directory) / "codex").resolve()
                                   for directory in os.get_exec_path())
        for candidate in candidates:
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            try:
                version = subprocess.run([str(candidate), "--version"], capture_output=True,
                                         text=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if version.returncode == 0 and version.stdout.strip() == f"codex-cli {host_version}":
                return str(candidate)
    return primary


def _read_allowlist(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not payload.get("files"):
        raise ValueError("read allowlist requires schema_version=1 and files")
    seen: set[str] = set()
    for entry in payload["files"]:
        source = Path(entry["path"])
        if not source.is_absolute() or source.resolve() != source:
            raise ValueError("allowed files must use absolute canonical paths")
        _require_file(source, "allowed file")
        if entry["path"] in seen or _sha256(source) != entry["sha256"]:
            raise ValueError(f"duplicate or changed allowed file: {source}")
        seen.add(entry["path"])
    denied = payload.get("denied_paths")
    if not denied or any(not Path(value).is_absolute() for value in denied):
        raise ValueError("read allowlist requires absolute denied_paths for verification")
    if any(str(Path(value).resolve()) in seen for value in denied):
        raise ValueError("a denied file is in the read allowlist")
    return payload


def _source_paths(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"path", "relative_path", "source_path"} and isinstance(child, str):
                yield child
            else:
                yield from _source_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _source_paths(child)


def build_diagnosis_allowlist(run_dir: Path, project: Path) -> dict[str, Any]:
    project = project.resolve()
    request = json.loads((run_dir / "03_integrated_diagnosis_request.json").read_text(encoding="utf-8"))
    if Path(request["source_locator"]["project_root"]).resolve() != project:
        raise ValueError("request project identity differs from diagnostic project")
    paths = {run_dir / name for name in (
        "00_experience.md", "01_evidence_bundle.json", "03_integrated_diagnosis_request.json",
        "03_integrated_diagnosis_prompt.md", "03_integrated_diagnosis_output_schema.json",
        "03_integrated_diagnosis_result_template.json")}
    evidence = request["evidence"]
    for entry in evidence["source_files"]:
        path = (project / entry["relative_path"]).resolve()
        if not path.is_relative_to(project / "raw") or _sha256(path) != entry["sha256"]:
            raise ValueError(f"raw source identity changed: {path}")
        paths.add(path)
    for value in _source_paths(evidence):
        path = (project / value).resolve()
        if path.is_file() and any(path.is_relative_to(project / part) for part in ("raw", "specs")):
            paths.add(path)
    for entry in evidence["curve_images"] + evidence["specification_images"]:
        path = Path(entry["image_path"]).resolve()
        if _sha256(path) != entry["image_sha256"]:
            raise ValueError(f"image identity changed: {path}")
        paths.add(path)
    local_specifications = {
        path.resolve() for path in (project / "specs" / request["valve_tag"]).rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    }
    unbound = local_specifications - {path.resolve() for path in paths}
    if unbound:
        raise ValueError("unbound specification originals require source/parameter review: "
                         + ", ".join(str(path) for path in sorted(unbound)))
    denied = [project / "project.toml", project / "output", project / "experience/feedback_inbox",
              project / "work/stage-gates", Path.home() / ".codex/sessions",
              Path.home() / ".codex/history.jsonl"]
    return {"schema_version": 1, "valve_tag": request["valve_tag"],
            "files": [{"path": str(path.resolve()), "sha256": _sha256(path)} for path in sorted(paths)],
            "denied_paths": sorted({str(path.resolve()) for path in denied})}


def _python_runtime_roots(payload: dict[str, Any]) -> list[Path]:
    """Expose this interpreter's runtime, including venv base stdlib, read-only.

    Python can be installed outside /usr, and prefix/exec_prefix may differ.
    Bind only the concrete installation roots, never their common parent.
    Keep both spellings for symlinked installations so Python's own path
    discovery and the interpreter executable resolve inside the namespace.
    """
    home = Path.home().resolve()
    auth = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).resolve()
    protected = [home, auth, *(Path(value).resolve()
                              for value in payload.get("denied_paths", []))]
    broad = {Path(value) for value in ("/", "/opt", "/home", "/root", "/tmp",
                                      "/var", "/workspace", "/mnt", "/srv")}
    roots: list[Path] = []
    for value in (sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix):
        path = Path(value).absolute()
        resolved = path.resolve()
        # /usr is already an intentional fixed runtime mount. All additional
        # roots must not expose a home, authentication store or excluded data.
        if (resolved in broad
                or any(target.is_relative_to(resolved) for target in protected)
                or any(resolved.is_relative_to(target) for target in protected[1:])):
            raise RuntimeError(f"unsafe Python runtime root: {path}")
        if not resolved.is_dir():
            raise RuntimeError(f"missing Python runtime root: {path}")
        for candidate in (path, resolved):
            if candidate not in roots and not candidate.is_relative_to(Path("/usr")):
                roots.append(candidate)
    return roots


def _isolation_prefix(
    payload: dict[str, Any], output_dir: Path, codex: str,
) -> tuple[list[str], dict[str, str]]:
    """An empty filesystem with individual evidence files and fixed runtimes.

    The Codex process itself lives inside this namespace; a prompt or an inner
    read-only shell policy alone cannot hide host reports and session history.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("bwrap is required for evidence-only model calls")
    command = [bwrap, "--unshare-all", "--share-net", "--die-with-parent",
               "--new-session", "--proc", "/proc", "--dev", "/dev",
               "--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr"]
    for name in ("bin", "sbin", "lib", "lib64"):
        path = Path("/") / name
        if path.is_symlink():
            command.extend(("--symlink", os.readlink(path), str(path)))
        elif path.exists():
            command.extend(("--ro-bind", str(path), str(path)))
    for name in ("resolv.conf", "hosts", "nsswitch.conf", "passwd", "group",
                 "ld.so.cache", "ssl/certs", "fonts"):
        path = Path("/etc") / name
        if path.exists():
            command.extend(("--ro-bind", str(path), str(path)))
    executable = Path(codex).resolve()
    runtime = executable.parent.parent if executable.name == "codex.js" else executable.parent
    command.extend(("--ro-bind", str(runtime), str(runtime)))
    for python_root in _python_runtime_roots(payload):
        command.extend(("--ro-bind", str(python_root), str(python_root)))
    # Preserve the configured identity, but expose only its authentication file,
    # never the user's config, skills, plugins, sessions, or history database.
    auth_directory = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    auth = _require_file(auth_directory / "auth.json", "Codex authentication")
    command.extend(("--ro-bind", str(auth), str(auth_directory / "auth.json")))
    catalog = auth_directory / "models_cache.json"
    if catalog.is_file():
        command.extend(("--ro-bind", str(catalog), str(catalog)))
    for entry in payload["files"]:
        command.extend(("--ro-bind", entry["path"], entry["path"]))
    command.extend(("--bind", str(output_dir), "/tmp/replay-result", "--chdir", "/tmp"))
    permitted_environment = {"HOME", "CODEX_HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
                             "TZ", "OPENAI_API_KEY", "OPENAI_ORG_ID",
                             "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "HTTP_PROXY", "HTTPS_PROXY",
                             "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    env = {key: value for key, value in os.environ.items() if key in permitted_environment}
    env["PATH"] = f"{sys.prefix}/bin:/usr/bin:/bin"
    return command, env


def _check_isolation(
    prefix: list[str], env: dict[str, str], payload: dict[str, Any], codex: str,
    *, timeout: float | None = None,
) -> dict[str, Any]:
    probe = r'''
import hashlib, json, os, pathlib, subprocess, sys
p = json.loads(sys.stdin.read())
for item in p['files']:
    path = pathlib.Path(item['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], str(path)
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError:
        pass
    else:
        os.close(fd)
        raise AssertionError('input is writable: ' + str(path))
for value in p['denied_paths']:
    assert not pathlib.Path(value).exists(), 'excluded path is visible: ' + value
    link = pathlib.Path('/tmp/denied-link')
    link.symlink_to(value)
    assert not link.exists(), 'symlink escaped namespace'
    link.unlink()
out = pathlib.Path('/tmp/replay-result/probe')
out.write_text('writable output')
out.unlink()
version = subprocess.run([p['codex'], '--version'], capture_output=True, text=True, check=True)
print(json.dumps({'status': 'passed', 'allowed_files': len(p['files']),
 'denied_paths': len(p['denied_paths']), 'inputs_read_only': True,
 'symlink_escape': False, 'codex_version': version.stdout.strip()}))
'''
    result = subprocess.run(prefix + ["--", sys.executable, "-c", probe],
                            input=json.dumps({**payload, "codex": str(Path(codex).resolve())}),
                            text=True, capture_output=True, env=env, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"evidence isolation check failed: {result.stderr[-4000:]}")
    return json.loads(result.stdout)


def _isolated_run(
    args: argparse.Namespace, command: list[str], prompt: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    allowlist = _require_file(args.read_allowlist, "read allowlist")
    payload = _read_allowlist(allowlist)
    required = [args.prompt_file, args.output_schema, *args.image]
    permitted = {item["path"] for item in payload["files"]}
    if any(str(path.resolve()) not in permitted for path in required if path):
        raise ValueError("prompt, schema and every image must be in the read allowlist")
    if not (args.ignore_user_config and args.ignore_rules) or args.cwd.resolve() != Path("/tmp"):
        raise ValueError("isolated calls require /tmp, --ignore-user-config and --ignore-rules")
    destination = args.output_last_message
    if destination is None or destination.exists():
        raise ValueError("isolated calls require a new output-last-message file")
    with tempfile.TemporaryDirectory(prefix="valve-codex-") as temporary:
        prefix, env = _isolation_prefix(payload, Path(temporary), command[0])
        check = _check_isolation(prefix, env, payload, command[0])
        check.update(allowlist_sha256=_sha256(allowlist), checked_at=_utc_now())
        with (allowlist.parent / f"isolation_check.{args.call_id}.json").open("x", encoding="utf-8") as handle:
            json.dump(check, handle, ensure_ascii=False, indent=2)
        isolated_command = list(command)
        isolated_command[0] = str(Path(command[0]).resolve())
        isolated_command[isolated_command.index("--output-last-message") + 1] = "/tmp/replay-result/result.json"
        result = subprocess.run(prefix + ["--", *isolated_command], input=prompt,
                                capture_output=True, text=True, env=env, check=False)
        produced = Path(temporary) / "result.json"
        if result.returncode == 0 and produced.is_file():
            try:
                with destination.open("xb") as handle:
                    handle.write(produced.read_bytes())
            except OSError as exc:
                result = subprocess.CompletedProcess(result.args, 74, result.stdout,
                    result.stderr + f"\nResult copy failed: {type(exc).__name__}: {exc}\n")
    return result, check


def _run(args: argparse.Namespace) -> int:
    if (args.model, args.reasoning_effort) != ("gpt-6-sol", "high"):
        raise ValueError("new diagnostic calls require gpt-6-sol / high; no automatic fallback")
    if args.stage in {"integrated_diagnosis", "independent_replay"} and not getattr(args, "read_allowlist", None):
        args.read_allowlist = args.prompt_file.parent / "allowed_reads.json"
        _require_file(args.read_allowlist, "diagnostic read allowlist")
    project_root = args.project_root.resolve()
    directory, ledger, _ = _telemetry_paths(project_root)
    prompt_file = _require_file(args.prompt_file, "prompt")
    images = [_require_file(path, "image") for path in args.image]
    schema = (
        _require_file(args.output_schema, "output schema")
        if args.output_schema
        else None
    )
    output_last_message = (
        args.output_last_message.resolve()
        if args.output_last_message
        else None
    )
    if output_last_message:
        output_last_message.parent.mkdir(parents=True, exist_ok=True)
    if not CALL_ID_PATTERN.fullmatch(args.call_id):
        raise ValueError(f"invalid call id: {args.call_id!r}")
    codex = _codex_executable(getattr(args, "codex_bin", None))
    if ledger.is_file():
        with ledger.open(encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            if any(item["call_id"] == args.call_id for item in _read_records(handle)):
                raise ValueError(f"duplicate model call id: {args.call_id}")

    command = [
        codex,
        "exec",
        "-m",
        args.model,
        "-c",
        f'model_reasoning_effort="{args.reasoning_effort}"',
        "-C",
        str(args.cwd.resolve()),
        "--skip-git-repo-check",
        "--sandbox",
        args.sandbox_mode,
        "--ephemeral",
    ]
    if args.ignore_user_config:
        command.append("--ignore-user-config")
    if args.ignore_rules:
        command.append("--ignore-rules")
    if schema:
        command.extend(("--output-schema", str(schema)))
    if output_last_message:
        command.extend(("--output-last-message", str(output_last_message)))
    for image in images:
        command.extend(("-i", str(image)))
    command.append("-")

    env = os.environ.copy()
    skill_runtime = runtime_identity()
    # Reserve before invoking the model; concurrent/retried IDs cannot overwrite logs.
    log_path = directory / f"{args.call_id}.log"
    try:
        log_path.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"model call log already exists; use a new call id: {log_path}") from exc
    started_at = _utc_now()
    started = time.monotonic()
    isolation = None
    try:
        if getattr(args, "read_allowlist", None):
            result, isolation = _isolated_run(args, command, prompt_file.read_text(encoding="utf-8"))
        else:
            result = subprocess.run(
                command,
                input=prompt_file.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
    except Exception as exc:
        result = subprocess.CompletedProcess(command, 70, "", f"{type(exc).__name__}: {exc}\n")
    elapsed = round(time.monotonic() - started, 3)
    ended_at = _utc_now()
    combined = result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr
    tokens = parse_tokens(combined)
    log_path.write_text(combined, encoding="utf-8")
    record = {
        "schema_version": 1,
        "event_type": "model_call",
        "call_id": args.call_id,
        "stage": args.stage,
        "valve_tag": args.valve_tag or "",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "codex_executable": str(Path(codex).resolve()),
        "skill_runtime": skill_runtime,
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": elapsed,
        "tokens_used": tokens,
        "status": "completed" if result.returncode == 0 else "failed",
        "return_code": result.returncode,
        "prompt_sha256": _sha256(prompt_file),
        "image_count": len(images),
        "image_sha256s": [_sha256(path) for path in images],
        "output_schema_sha256": _sha256(schema) if schema else "",
        "log": log_path.relative_to(project_root).as_posix(),
    }
    if isolation is not None:
        record["read_isolation"] = isolation
    summary_path = _append_record(project_root, record)
    print(f"MODEL_CALL_ID={args.call_id}")
    print(f"MODEL_CALL_STATUS={record['status']}")
    print(f"MODEL_CALL_RETURN_CODE={result.returncode}")
    print(f"MODEL_CALL_TOKENS={tokens if tokens is not None else 'unknown'}")
    print(f"MODEL_CALL_ELAPSED_SECONDS={elapsed}")
    print(f"MODEL_CALL_LOG={log_path}")
    if result.returncode == 0 and output_last_message and output_last_message.is_file():
        print(f"MODEL_CALL_RESULT={output_last_message}")
    print(f"WORKFLOW_SUMMARY={summary_path}")
    return result.returncode


def _mark(args: argparse.Namespace) -> int:
    record = {
        "schema_version": 1,
        "event_type": "outcome",
        "call_id": args.call_id,
        "recorded_at": _utc_now(),
        "outcome": args.outcome,
        "reason": args.reason,
    }
    summary_path = _append_record(args.project_root, record)
    print(f"MODEL_CALL_OUTCOME={args.outcome}")
    print(f"WORKFLOW_SUMMARY={summary_path}")
    return 0


def _summary(args: argparse.Namespace) -> int:
    _, ledger, _ = _telemetry_paths(args.project_root)
    if not ledger.is_file():
        summary = _build_summary([])
    else:
        with ledger.open(encoding="utf-8") as handle:
            summary = _build_summary(_read_records(handle))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--project-root", type=Path, required=True)
    run.add_argument("--stage", required=True)
    run.add_argument("--call-id", required=True)
    run.add_argument("--valve-tag")
    run.add_argument("--model", default="gpt-6-sol", choices=("gpt-6-sol",))
    run.add_argument("--reasoning-effort", default="high", choices=("high",))
    run.add_argument("--prompt-file", type=Path, required=True)
    run.add_argument("--image", type=Path, action="append", default=[])
    run.add_argument("--output-schema", type=Path)
    run.add_argument("--output-last-message", type=Path)
    run.add_argument("--cwd", type=Path, default=Path("/tmp"))
    run.add_argument("--ignore-user-config", action="store_true")
    run.add_argument("--ignore-rules", action="store_true")
    run.add_argument("--read-allowlist", type=Path,
                     help="SHA-bound evidence files exposed to an isolated Codex process")
    run.add_argument("--codex-bin", type=Path, help="Explicit installed Codex executable for this call")
    run.add_argument(
        "--sandbox-mode",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
        help=(
            "Codex sandbox policy (default read-only). danger-full-access is "
            "required on hosts where bwrap cannot create a network namespace."
        ),
    )
    mark = commands.add_parser("mark")
    mark.add_argument("--project-root", type=Path, required=True)
    mark.add_argument("--call-id", required=True)
    mark.add_argument("--outcome", choices=("accepted", "rejected"), required=True)
    mark.add_argument("--reason", default="")
    summary = commands.add_parser("summary")
    summary.add_argument("--project-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "mark":
        return _mark(args)
    return _summary(args)


if __name__ == "__main__":
    raise SystemExit(main())
