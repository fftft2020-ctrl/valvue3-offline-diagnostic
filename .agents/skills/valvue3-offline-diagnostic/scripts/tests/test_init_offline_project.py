from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

from offline_engine.config import load_project


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
INIT_SCRIPT = SCRIPTS_DIR / "init_offline_project.py"
REPORT_SCRIPT = SCRIPTS_DIR / "run_offline_report.py"


def _initialize(project: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    values = {
        "customer": '示例“北区”\\装置',
        "report_title": "Masoneilan ValVue3 离线诊断报告",
        "report_date": "2026-08-26",
    }
    values.update(overrides)
    return subprocess.run(
        [
            sys.executable,
            str(INIT_SCRIPT),
            "--project",
            str(project),
            "--customer",
            values["customer"],
            "--report-title",
            values["report_title"],
            "--report-date",
            values["report_date"],
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_initializer_creates_portable_project_that_passes_dry_run(
    tmp_path: Path,
) -> None:
    project = tmp_path / "new-offline-project"

    initialized = _initialize(project)

    assert initialized.returncode == 0, initialized.stderr
    assert "status=initialized" in initialized.stdout
    assert "run_offline_report.py --prepare-materials" in initialized.stdout
    for relative in (
        "raw",
        "specs",
        "inbound",
        "experience/feedback_inbox",
        "work",
        "output",
    ):
        assert (project / relative).is_dir()

    raw_config = tomllib.loads(
        (project / "project.toml").read_text(encoding="utf-8")
    )
    assert raw_config["project"] == {
        "customer": '示例“北区”\\装置',
        "report_title": "Masoneilan ValVue3 离线诊断报告",
        "report_date": "2026-08-26",
    }
    assert raw_config["paths"] == {
        "raw": "raw",
        "specs": "specs",
        "inbound": "inbound",
        "work": "work",
        "output": "output",
        "output_docx": "ValVue3-Offline-Diagnostic-Report.docx",
        "output_pdf": "ValVue3-Offline-Diagnostic-Report.pdf",
        "manual_interpretations": "work/manual_curve_interpretations.json",
    }
    assert raw_config["features"] == {
        "include_specifications": True,
        "include_air_piping_pages": True,
        "include_accessory_pages": False,
        "include_data_insufficient_valves": True,
        "convert_pdf": True,
        "allow_loose_pdf_metrics": True,
        "include_signature_metrics": True,
    }
    assert raw_config["presentation"] == {
        "profile": "business_dashboard",
        "brand_palette": "baker_hughes_deep_teal",
        "rating_palette": "green_gold_orange_red_gray",
        "spec_placement": "after_each_valve",
        "include_field_context_overview": False,
        "include_overview_judgement": False,
        "include_overview_priority": False,
        "include_retest_recommendations": False,
    }
    assert "formal_diagnosis_run" not in raw_config["paths"]

    assert not (project / "experience/project_experience.json").exists()
    extraction_overrides = json.loads(
        (project / "experience/extraction_overrides.json").read_text(
            encoding="utf-8"
        )
    )
    assert extraction_overrides == {
        "schema_version": 1,
        "valves": {},
    }
    assert not (project / "experience/experience_log.md").exists()
    assert not list(project.rglob("*.py"))
    assert not (project / "experience/common-offline-experience.json").exists()
    assert not (project / "experience/common-offline-experience.md").exists()

    config = load_project(project)
    assert config.formal_diagnosis_run_dir is None
    assert config.raw_dir == project / "raw"
    assert config.presentation["profile"] == "business_dashboard"
    assert config.features["include_air_piping_pages"] is True
    assert config.features["include_accessory_pages"] is False
    assert config.migration_notices == ()

    dry_run = subprocess.run(
        [
            sys.executable,
            str(REPORT_SCRIPT),
            "--project",
            str(project),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "status=dry_run" in dry_run.stdout
    manifest = json.loads(
        (project / "work/run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "dry_run"
    assert manifest["project"]["customer"] == '示例“北区”\\装置'


def test_initializer_refuses_to_overwrite_an_existing_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "existing-project"
    first = _initialize(project)
    assert first.returncode == 0, first.stderr
    before = {
        relative: (project / relative).read_bytes()
        for relative in (
            "project.toml",
            "experience/extraction_overrides.json",
        )
    }

    second = _initialize(
        project,
        customer="不应覆盖的客户",
        report_title="不应覆盖的报告",
    )

    assert second.returncode != 0
    assert "refusing to overwrite existing project files" in second.stderr
    assert {
        relative: (project / relative).read_bytes()
        for relative in before
    } == before

    help_result = subprocess.run(
        [sys.executable, str(INIT_SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--force" not in help_result.stdout


def test_initializer_rejects_invalid_date_before_creating_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "invalid-date"

    result = _initialize(project, report_date="2026-02-30")

    assert result.returncode != 0
    assert "valid calendar date" in result.stderr
    assert not project.exists()
