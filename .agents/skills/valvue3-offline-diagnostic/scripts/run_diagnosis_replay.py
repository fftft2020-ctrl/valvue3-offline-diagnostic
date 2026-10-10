#!/usr/bin/env python3
"""Prepare, meter, seal and compare independent diagnostic replays.

This only orchestrates existing evidence and diagnosis commands. Reference
answers are accepted exclusively by `compare`, after prediction sealing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

from formal_diagnosis_tools import single_valve_workflow as workflow
from offline_engine.config import load_project


SCRIPTS = Path(__file__).resolve().parent
WORKFLOW = SCRIPTS / "formal_diagnosis_tools/single_valve_workflow.py"
METER = SCRIPTS / "run_metered_codex.py"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_new(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invoke(*arguments: str | Path) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, *map(str, arguments)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {result.stderr[-5000:]}")
    return result


def code_hashes() -> dict[str, str]:
    return {str(path.relative_to(SCRIPTS)): digest(path)
            for path in sorted(SCRIPTS.rglob("*.py")) if "tests" not in path.parts}


def prepare(args) -> None:
    plan = read(args.plan)
    if (plan["model"], plan["reasoning_effort"]) != ("gpt-6.1-sol", "high"):
        raise ValueError("new replays require gpt-6.1-sol / high")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,55}", args.round):
        raise ValueError("invalid round identifier")
    project = Path(plan["project_root"]).resolve()
    evidence_round = args.evidence_round or args.round
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,55}", evidence_round):
        raise ValueError("invalid evidence round identifier")
    selected = plan["trial" if args.selection == "trial" else "all_targets"]
    tags = [tag for values in selected.values() for tag in values]
    if not tags or len(tags) != len(set(tags)):
        raise ValueError("target tags must be nonempty and unique")
    root = Path(plan["output_root"]).resolve() / args.round
    root.mkdir(parents=True, exist_ok=False)
    write_new(root / "plan.json", plan)
    entries = []
    for period, period_tags in selected.items():
        config = load_project(project, report_period=period)
        expected = set(config.report_layout.get("only_valves") or [])
        if not set(period_tags).issubset(expected):
            raise ValueError(f"targets outside selected period: {period}")
        evidence_dir = project / "work/replay-inputs" / evidence_round / period
        summary = evidence_dir / "analysis_summary.json"
        if summary.exists():
            workflow.resolve_project_analysis_summary(project, summary, report_period=period)
        else:
            invoke(SCRIPTS / "run_offline_report.py", "--project", project,
                   "--report-period", period, "--prepare-formal-evidence",
                   "--evidence-work-dir", evidence_dir)
        command = [WORKFLOW, "prepare-project", "--project-root", project,
                   "--project-id", "independent-replay", "--report-period", period,
                   "--model", plan["model"], "--reasoning-effort", plan["reasoning_effort"],
                   "--run-root", root / period, "--diagnosis-version", f"{args.round}-{period}",
                   "--parent-run", "none", "--revision-reason", "独立历史回放；不继承旧判断",
                   "--analysis-summary", summary]
        for tag in period_tags:
            command.extend(("--only-valve", tag))
        invoke(*command)
        for tag in period_tags:
            run_dir = root / period / tag
            entry = {"tag": tag, "period": period, "run_dir": str(run_dir),
                     "reference_batch": plan.get("reference_batches", {}).get(period)}
            try:
                invoke(WORKFLOW, "prepare-integrated-diagnosis", "--run-dir", run_dir)
                entry["allowlist_sha256"] = digest(run_dir / "allowed_reads.json")
            except RuntimeError as exc:
                if "unbound specification originals" not in str(exc):
                    raise
                entry["preparation_error"] = str(exc)
                write_new(run_dir / "preparation_issue.json", {"status": "input_blocked", "reason": str(exc)})
            entries.append(entry)
        print(f"PREPARED_PERIOD={period} VALVES={len(period_tags)}", flush=True)
    write_new(root / "replay.json", {"schema_version": 1, "status": "prepared",
        "prepared_at": now(), "project_root": str(project), "round": args.round,
        "evidence_round": evidence_round,
        "model": plan["model"], "reasoning_effort": plan["reasoning_effort"],
        "maximum_parallel_valves": 1, "entries": entries, "code_sha256": code_hashes(),
        "experience_sha256": digest(Path(plan["experience_source"]))})
    print(f"REPLAY_ROOT={root}", flush=True)


def _call_one(root: Path, replay: dict, entry: dict) -> dict:
    run_dir = Path(entry["run_dir"])
    if entry.get("preparation_error"):
        outcome = {"tag": entry["tag"], "status": "input_blocked", "reason": entry["preparation_error"]}
        write_new(run_dir / "execution.json", outcome)
        print(f"INPUT_BLOCKED {entry['tag']}", flush=True)
        return outcome
    if (run_dir / "execution.json").exists():
        existing = read(run_dir / "execution.json")
        if existing["status"] == "accepted":
            return existing
        raise ValueError("rejected execution must be investigated before a new round")
    allowlist = run_dir / "allowed_reads.json"
    if digest(allowlist) != entry["allowlist_sha256"]:
        raise ValueError("prepared allowlist changed")
    request = read(run_dir / workflow.INTEGRATED_REQUEST_FILENAME)
    call_id = f"independent.{replay['round']}.{entry['tag']}.attempt-1"
    result_path = run_dir / "03_integrated_diagnosis_result.json"
    command = [sys.executable, str(METER), "run", "--project-root", replay["project_root"],
               "--stage", "independent_replay", "--call-id", call_id, "--valve-tag", entry["tag"],
               "--model", replay["model"], "--reasoning-effort", replay["reasoning_effort"],
               "--cwd", "/tmp", "--ignore-user-config", "--ignore-rules", "--sandbox-mode", "read-only",
               "--read-allowlist", str(allowlist),
               "--output-schema", str(run_dir / workflow.INTEGRATED_SCHEMA_FILENAME),
               "--output-last-message", str(result_path),
               "--prompt-file", str(run_dir / workflow.INTEGRATED_PROMPT_FILENAME)]
    for image in request["evidence"]["curve_images"] + request["evidence"]["specification_images"]:
        command.extend(("--image", image["image_path"]))
    outcome = {"tag": entry["tag"], "call_id": call_id, "started_at": now(), "status": "rejected"}
    print(f"START {entry['tag']}", flush=True)
    with (run_dir / "transport.log").open("x", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        outcome["failure_phase"] = "transport"
        outcome["reason"] = f"model transport exit {result.returncode}; see transport.log"
    else:
        try:
            invoke(WORKFLOW, "submit-integrated-diagnosis", "--run-dir", run_dir, "--result", result_path)
            outcome["status"] = "accepted"
            outcome["reason"] = "来源、模型、结构及全部图像覆盖校验通过；工程比较待做"
        except Exception as exc:
            outcome["failure_phase"] = "submission"
            outcome["reason"] = str(exc)
    outcome["ended_at"] = now()
    if result_path.is_file():
        outcome["result_sha256"] = digest(result_path)
    try:
        invoke(METER, "mark", "--project-root", replay["project_root"], "--call-id", call_id,
               "--outcome", outcome["status"], "--reason", outcome["reason"])
    except RuntimeError as exc:
        outcome["meter_mark_error"] = str(exc)
    write_new(run_dir / "execution.json", outcome)
    print(f"{outcome['status'].upper()} {entry['tag']}", flush=True)
    return outcome


def run(args) -> None:
    root = args.run_root.resolve()
    replay = read(root / "replay.json")
    if (root / "sealed.json").exists() or replay["code_sha256"] != code_hashes():
        raise ValueError("replay sealed or implementation changed; prepare a new round")
    for entry in replay["entries"]:
        if digest(Path(entry["run_dir"]) / "00_experience.md") != replay["experience_sha256"]:
            raise ValueError("per-valve experience differs from the recorded round")
    if args.jobs != 1:
        raise ValueError("diagnosis calls must run serially (--jobs 1)")
    results = []
    for item in replay["entries"]:
        try:
            results.append(_call_one(root, replay, item))
        except Exception as exc:
            results.append({"tag": item["tag"], "status": "rejected", "reason": str(exc),
                            "failure_phase": "transport"})
            print(f"REJECTED {item['tag']}: {exc}", flush=True)
        if results[-1].get("failure_phase") == "transport":
            break  # Investigate a runtime/model failure before repeating it on more valves.
    summary = {"completed_at": now(), "total": len(replay["entries"]),
               "attempted": sum(item["status"] != "input_blocked" for item in results),
               "input_blocked": sum(item["status"] == "input_blocked" for item in results),
               "pending": len(replay["entries"]) - len(results),
               "accepted": sum(item["status"] == "accepted" for item in results), "results": results}
    write_new(root / "execution_summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}))


def snapshot(args) -> None:
    root = args.run_root.resolve()
    replay = read(root / "replay.json")
    if replay["code_sha256"] != code_hashes():
        raise ValueError("implementation changed after preparation")
    blocked = []
    for item in replay["entries"]:
        if digest(Path(item["run_dir"]) / "00_experience.md") != replay["experience_sha256"]:
            raise ValueError("per-valve experience differs from the recorded round")
        execution = read(Path(item["run_dir"]) / "execution.json")
        if execution["status"] != "accepted":
            if not args.allow_partial:
                raise ValueError(f"cannot seal incomplete replay: {item['tag']}")
            blocked.append({"tag": item["tag"], "status": execution["status"], "reason": execution.get("reason")})
            continue
        if execution.get("meter_mark_error"):
            raise ValueError(f"meter outcome was not registered: {item['tag']}")
        if digest(Path(item["run_dir"]) / "03_integrated_diagnosis_result.json") != execution["result_sha256"]:
            raise ValueError(f"accepted prediction changed: {item['tag']}")
        allowlist = read(Path(item["run_dir"]) / "allowed_reads.json")
        for source in allowlist["files"]:
            if digest(Path(source["path"])) != source["sha256"]:
                raise ValueError(f"input changed since diagnosis: {source['path']}")
    files = {str(path.relative_to(root)): digest(path) for path in sorted(root.rglob("*"))
             if path.is_file() and path.name != "sealed.json"}
    write_new(root / "sealed.json", {"schema_version": 1, "status": "sealed_for_comparison",
                                     "sealed_at": now(), "files": files, "excluded_results": blocked})
    print(f"SEALED={root / 'sealed.json'}")


def compare(args) -> None:
    root = args.run_root.resolve()
    seal = read(root / "sealed.json")
    if seal["status"] != "sealed_for_comparison":
        raise ValueError("predictions must be sealed before opening references")
    for relative, expected in seal["files"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"sealed evidence changed: {relative}")
    # No earlier phase reads the reference file.
    reference = read(args.reference)
    indexed = {(batch["batch"], tag): {"batch": batch["batch"], **item}
               for batch in reference["batches"] for tag, item in batch["entries"].items()}
    rows = []
    for item in read(root / "replay.json")["entries"]:
        if any(entry["tag"] == item["tag"] for entry in seal.get("excluded_results", [])):
            continue
        prediction = read(Path(item["run_dir"]) / "03_integrated_diagnosis_result.json")
        expected = indexed[(item["reference_batch"], item["tag"])]
        whole = prediction["whole_valve"]
        rating = expected["fields"]["rating"]
        eligible = rating["comparison_eligible"]
        rows.append({"tag": item["tag"], "model_rating": whole["final_rating"],
                     "reference_rating": rating["value"], "rating_comparable": eligible,
                     "rating_equal": whole["final_rating"] == rating["value"] if eligible else None,
                     "prediction": whole, "reference": expected,
                     "engineering_review": "pending"})
    payload = {"status": "comparison_prepared_engineering_review_pending", "created_at": now(),
               "reference_sha256": digest(args.reference), "sealed_sha256": digest(root / "sealed.json"),
               "excluded_results": seal.get("excluded_results", []),
               "total": len(rows), "rating_comparable": sum(r["rating_comparable"] for r in rows),
               "rating_equal": sum(r["rating_equal"] is True for r in rows), "rows": rows}
    write_new(root / "comparison.json", payload)
    with (root / "comparison.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["位号", "新诊断等级", "回传等级", "等级可比", "等级一致", "新诊断问题", "回传问题", "问题可比", "问题来源", "新诊断建议", "回传建议", "建议可比", "建议来源", "评级参考资格说明"])
        for row in rows:
            fields = row["reference"]["fields"]
            writer.writerow([row["tag"], row["model_rating"], row["reference_rating"], row["rating_comparable"],
                             row["rating_equal"], row["prediction"].get("main_issue"), fields.get("issues", {}).get("value"),
                             fields.get("issues", {}).get("comparison_eligible"), fields.get("issues", {}).get("provenance_class"),
                             json.dumps(row["prediction"].get("actions"), ensure_ascii=False), fields.get("actions", {}).get("value"),
                             fields.get("actions", {}).get("comparison_eligible"), fields.get("actions", {}).get("provenance_class"),
                             fields["rating"]["eligibility_reason"]])
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--plan", type=Path, required=True)
    prep.add_argument("--selection", choices=("trial", "all"), required=True)
    prep.add_argument("--round", required=True)
    prep.add_argument("--evidence-round", help="Reuse an existing source-bound fact preparation round")
    running = commands.add_parser("run")
    running.add_argument("--jobs", type=int, choices=(1,), default=1)
    snap = commands.add_parser("snapshot")
    snap.add_argument("--allow-partial", action="store_true", help="Seal completed predictions and explicitly list excluded results")
    comp = commands.add_parser("compare")
    comp.add_argument("--reference", type=Path, required=True)
    for command in (running, snap, comp):
        command.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "snapshot": snapshot, "compare": compare}[args.command](args)


if __name__ == "__main__":
    main()
