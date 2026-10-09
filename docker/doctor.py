"""Stage-specific readiness; no paid model calls and no client data reads."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("all", "docx", "diagnosis", "ocr", "wps_pdf"), default="all")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(os.environ["SKILL_DIR"]) / "scripts"))
    from check_environment import check_environment
    report = check_environment()
    report["requested_stage"] = args.stage
    print(json.dumps(report, ensure_ascii=False, indent=2))
    stages = report["stages"].values() if args.stage == "all" else [report["stages"][args.stage]]
    return int(any(stage["status"] == "blocked" for stage in stages))


if __name__ == "__main__":
    raise SystemExit(main())
