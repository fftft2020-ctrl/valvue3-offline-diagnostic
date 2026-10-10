import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from docx import Document

from offline_engine.config import load_project
from offline_engine import pipeline
from offline_engine.evidence_extraction import EXTRACTOR_VERSION
from offline_engine.formal_diagnosis import FormalDiagnosis
from offline_engine.pipeline import (
    check_report_information,
    convert_docx_to_pdf,
    curve_review_finalize_status,
    generate_business_report,
    generate_review_report,
    ingest_review_report,
    prepare_formal_evidence,
    prepare_project_ocr,
    run_project,
)
from offline_engine.project_materials import (
    approve_parameter_extraction,
    write_parameter_extraction_report,
)
from offline_engine.visual_review import CurveInterpretation


def _register_review_source(project: Path, source: Path) -> None:
    config = project / "project.toml"
    if "formal_diagnosis_run" not in config.read_text():
        config.write_text(config.read_text().replace('[paths]', '[paths]\nformal_diagnosis_run = "work/formal"'))
    root = pipeline.load_project(project).formal_diagnosis_run_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "00_project_run_manifest.json").write_text('{"stage":"frozen"}')
    manifest = project / "work/review/v1/run_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "outputs": {
                    "docx": str(source),
                    "docx_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "formal_diagnosis": {
                    "tree_sha256": pipeline._tree_sha256(root),
                    "project_manifest_sha256": pipeline._sha256(root / "00_project_run_manifest.json"),
                },
            }
        ),
        encoding="utf-8",
    )


def test_retired_spec_ocr_command_explains_supported_replacement(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--prepare-spec-ocr"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "retired" in result.stderr
    assert "--extract-materials" in result.stderr


def test_report_cli_exposes_wps_delivery_validation_options() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "run_offline_report.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--validate-wps-pdf" in result.stdout
    assert "--delivery-docx" in result.stdout
    assert "--approve-wps-layout" in result.stdout
    assert "--check-report-information" in result.stdout
    assert "--check-report-pdf" in result.stdout
    assert "--approve-materials" in result.stdout
    assert "--approve-parameters" in result.stdout


def test_report_cli_runs_single_information_acceptance(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "raw").mkdir(parents=True)
    (project / "work").mkdir()
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "report.docx"
""".strip(),
        encoding="utf-8",
    )
    (project / "work/analysis_summary.json").write_text(
        json.dumps([{"folder": "FV-101", "included_in_report": True}]),
        encoding="utf-8",
    )
    report = project / "report.docx"
    document = Document()
    document.add_paragraph("FV-101")
    document.save(report)
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project",
            str(project),
            "--check-report-information",
            str(report),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "status=information_complete" in result.stdout
    assert "information=valves:1/1 fields:0/0 curves:0/0 technical_pages:0/0" in result.stdout
    manifest = project / "work/information-acceptance/report.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["ok"] is True


def test_dry_run_loads_shared_engine_and_writes_manifest(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "ValVue3 Offline Diagnostic Report"
report_date = "2026-07-11"

    [paths]
raw = "raw"
specs = "specs"
inbound = "inbound"
work = "work"
output = "output"
output_docx = "Demo.docx"
output_pdf = "Demo.pdf"
""".strip(),
        encoding="utf-8",
    )

    result = run_project(tmp_path, dry_run=True)

    assert result.config.customer == "Demo"
    manifest = json.loads((tmp_path / "work/run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"]["skill"] == "valvue3-offline-diagnostic"
    assert "experience" not in manifest  # Reports consume frozen diagnoses, not the live library.
    assert manifest["status"] == "dry_run"
    assert len(manifest["inputs"]["tree_sha256"]) == 64
    assert len(manifest["engine"]["tree_sha256"]) == 64
    assert (
        manifest["engine"]["git_dirty"] is None
        or isinstance(manifest["engine"]["git_dirty"], bool)
    )
    assert result.config.features["include_air_piping_pages"] is True
    assert result.config.features["include_accessory_pages"] is False
    assert result.config.presentation["include_field_context_overview"] is False
    assert result.config.presentation["include_overview_judgement"] is False
    assert result.config.presentation["include_overview_priority"] is False
    assert result.config.presentation["include_retest_recommendations"] is False
    assert manifest["configuration"]["features"] == result.config.features
    assert manifest["configuration"]["presentation"] == result.config.presentation
    assert manifest["configuration"]["migration_notices"] == []


def test_legacy_accessory_switch_does_not_enable_air_piping_silently(
    tmp_path: Path,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
output_docx = "Demo.docx"
[features]
include_accessory_pages = true
        """.strip(),
        encoding="utf-8",
    )

    config = load_project(tmp_path)

    assert config.features["include_accessory_pages"] is True
    assert config.features["include_air_piping_pages"] is False
    assert config.migration_notices == (
        "features.include_accessory_pages no longer controls air-piping pages; "
        "set features.include_air_piping_pages explicitly",
    )

    result = run_project(tmp_path, dry_run=True)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["configuration"]["migration_notices"] == list(
        config.migration_notices
    )


def test_formal_evidence_receives_normalized_page_and_presentation_switches(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
[features]
include_air_piping_pages = false
include_accessory_pages = true
[presentation]
include_overview_priority = true
        """.strip(),
        encoding="utf-8",
    )
    from offline_engine import generator

    configured = {}

    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(
        generator,
        "configure",
        lambda config, **kwargs: configured.setdefault("config", config),
    )

    def fake_write_summary(_valves, **kwargs):
        assert kwargs["structured_evidence_project_root"] == tmp_path.resolve()
        (tmp_path / "work").mkdir()
        (tmp_path / "work/analysis_summary.json").write_text(
            "[]", encoding="utf-8"
        )

    monkeypatch.setattr(
        generator,
        "write_summary",
        fake_write_summary,
    )
    monkeypatch.setattr(generator, "prepare_valve_data", lambda **_kwargs: [])

    result = prepare_formal_evidence(tmp_path)

    assert result.status == "formal_evidence_prepared"
    assert configured["config"].features["include_air_piping_pages"] is False
    assert configured["config"].features["include_accessory_pages"] is True
    assert configured["config"].presentation["include_overview_priority"] is True
    assert configured["config"].presentation["include_overview_judgement"] is False


def test_formal_evidence_embeds_fresh_structured_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw/FV-101").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
        """.strip(),
        encoding="utf-8",
    )
    from offline_engine import generator

    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(generator, "configure", lambda *args, **kwargs: None)

    def fake_write_summary(_valves, **kwargs):
        source = {"folder": "FV-101", "report_pages": []}
        source["structured_evidence"] = fake_structured_evidence(
            dict(source),
            project_root=kwargs["structured_evidence_project_root"],
        )
        (tmp_path / "work").mkdir()
        (tmp_path / "work/analysis_summary.json").write_text(
            json.dumps([source]),
            encoding="utf-8",
        )

    calls = []

    def fake_structured_evidence(source, *, project_root):
        calls.append((source, project_root))
        return {"schema_version": 1, "material_extraction": {"fields": {}}}

    monkeypatch.setattr(
        generator,
        "write_summary",
        fake_write_summary,
    )
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda **_kwargs: [generator.ValveReport(folder="FV-101")],
    )

    result = prepare_formal_evidence(tmp_path)
    summary = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert calls == [
        ({"folder": "FV-101", "report_pages": []}, tmp_path.resolve())
    ]
    assert summary[0]["structured_evidence"] == {
        "schema_version": 1,
        "material_extraction": {"fields": {}},
    }


def test_versioned_evidence_binds_period_and_preserves_existing_output(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "project.toml").write_text(
        '[project]\ncustomer="Demo"\nreport_title="Report"\nreport_date="2026-09-01"\n'
        '[paths]\nraw="raw"\nwork="work/current"\noutput_docx="Demo.docx"\n'
        '[report_periods]\nactive="batch"\nbatch_label="Batch"\n')
    current = tmp_path / "work/current"
    current.mkdir(parents=True)
    (current / "analysis_summary.json").write_text("old evidence")
    versioned = tmp_path / "work/replay/round-1"
    configured = {}

    def configure(config, **kwargs):
        configured.update(config=config, **kwargs)

    def write_summary(*args, **kwargs):
        destination = configured["config"].work_dir
        destination.mkdir(parents=True)
        (destination / "analysis_summary.json").write_text("[]")

    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(pipeline.generator, "configure", configure)
    monkeypatch.setattr(pipeline.generator, "prepare_valve_data", lambda: [])
    monkeypatch.setattr(pipeline.generator, "write_summary", write_summary)
    result = prepare_formal_evidence(tmp_path, report_period="batch", evidence_work_dir=versioned)
    origin = json.loads((versioned / "analysis_summary.source.json").read_text())
    assert origin["report_period_key"] == "batch"
    assert origin["summary_sha256"] == hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    assert not configured["load_formal_structured_evidence"]
    assert not configured["load_summary_overrides"]
    assert (current / "analysis_summary.json").read_text() == "old evidence"
    with pytest.raises(ValueError, match="already exists"):
        prepare_formal_evidence(tmp_path, evidence_work_dir=versioned)
    for invalid in (tmp_path / "elsewhere", current, tmp_path / "work"):
        with pytest.raises(ValueError, match="separate directory inside"):
            prepare_formal_evidence(tmp_path, evidence_work_dir=invalid)


def test_formal_evidence_isolated_from_configured_frozen_run(
    tmp_path: Path, monkeypatch
) -> None:
    """A fresh evidence snapshot must not inherit v2 report artifacts."""
    (tmp_path / "raw/FV-101").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
formal_diagnosis_run = "work/formal_diagnosis/runs/v2"
        """.strip(),
        encoding="utf-8",
    )
    from offline_engine import business_report, generator

    configure_calls = []
    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(
        generator,
        "configure",
        lambda config, **kwargs: configure_calls.append(kwargs),
    )

    def fake_write_summary(_valves, **kwargs):
        structured = {"source": "fresh-raw"}
        (tmp_path / "work").mkdir()
        (tmp_path / "work/analysis_summary.json").write_text(
            json.dumps(
                [
                    {
                        "folder": "FV-101",
                        "included_in_report": True,
                        "structured_evidence": structured,
                        "report_field_manifest": kwargs[
                            "report_field_manifest_builder"
                        ](generator.ValveReport(folder="FV-101"), structured),
                    }
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(generator, "write_summary", fake_write_summary)
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda **_kwargs: [generator.ValveReport(folder="FV-101")],
    )
    monkeypatch.setattr(
        business_report,
        "report_field_manifest",
        lambda valve, structured: [
            {"path": "fresh", "evidence_value": structured["source"], "display_text": "fresh"}
        ],
    )

    result = prepare_formal_evidence(tmp_path)
    summary = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert configure_calls[0]["load_formal_structured_evidence"] is False
    assert summary[0]["structured_evidence"] == {"source": "fresh-raw"}
    assert summary[0]["report_field_manifest"] == [
        {"path": "fresh", "evidence_value": "fresh-raw", "display_text": "fresh"}
    ]


def test_formal_evidence_rejects_stale_material_extraction(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw/FV-101").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
        """.strip(),
        encoding="utf-8",
    )
    from offline_engine import generator

    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(generator, "configure", lambda *args, **kwargs: None)

    def fake_write_summary(_valves, **kwargs):
        (tmp_path / "work").mkdir()
        (tmp_path / "work/analysis_summary.json").write_text(
            json.dumps([{"folder": "FV-101", "report_pages": []}]),
            encoding="utf-8",
        )
        reject_stale_evidence()

    def reject_stale_evidence(*args, **kwargs):
        raise ValueError("material extraction source hash is stale")

    monkeypatch.setattr(
        generator,
        "write_summary",
        fake_write_summary,
    )
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda **_kwargs: [generator.ValveReport(folder="FV-101")],
    )

    with pytest.raises(ValueError, match="source hash is stale"):
        prepare_formal_evidence(tmp_path)

    summary = json.loads(
        (tmp_path / "work/analysis_summary.json").read_text(encoding="utf-8")
    )
    assert "structured_evidence" not in summary[0]


def test_summary_builds_fresh_evidence_once_for_included_and_excluded_valves(
    tmp_path: Path, monkeypatch
) -> None:
    from offline_engine import evidence_extraction, generator

    monkeypatch.setattr(generator, "WORK_DIR", tmp_path)
    monkeypatch.setattr(generator, "SUMMARY_JSON", tmp_path / "analysis_summary.json")
    calls: list[str] = []

    def build(source, *, project_root):
        calls.append(source["folder"])
        return {"schema_version": 1, "source_record": {"folder": source["folder"]}}

    monkeypatch.setattr(evidence_extraction, "build_project_structured_evidence", build)
    valves = [
        generator.ValveReport(
            folder="FV-101",
            files=[generator.SourceFile("active.pdf", "PDF", pages=3)],
            spec_fields={"valve_size": "2 in"},
            dtm_info={"device_mode": "NORMAL"},
        ),
        generator.ValveReport(folder="FV-102"),
    ]
    records = generator.write_summary(
        valves,
        included_folders={"FV-101"},
        structured_evidence_project_root=tmp_path,
        report_field_manifest_builder=lambda valve, structured: [
            {"path": "identity.valve_tag", "display_text": valve.folder}
        ],
    )

    assert calls == ["FV-101", "FV-102"]
    assert records == json.loads(generator.SUMMARY_JSON.read_text(encoding="utf-8"))
    assert records[0]["files"] == [
        {"name": "active.pdf", "kind": "PDF", "pages": 3, "readable": True, "note": ""}
    ]
    assert records[0]["spec_fields"] == {"valve_size": "2 in"}
    assert records[0]["dtm_info"] == {"device_mode": "NORMAL"}
    assert records[0]["report_field_manifest"][0]["display_text"] == "FV-101"
    assert records[1]["structured_evidence"]["source_record"]["folder"] == "FV-102"
    assert "report_field_manifest" not in records[1]


def test_material_extraction_uses_normalized_switches_and_fresh_sources(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
[features]
include_air_piping_pages = true
include_accessory_pages = false
        """.strip(),
        encoding="utf-8",
    )
    from offline_engine import generator

    configured = {}
    monkeypatch.setattr(pipeline, "prepare_project_pdf_cache", lambda *args: {})
    monkeypatch.setattr(pipeline, "load_specification_pages", lambda *args: {})
    monkeypatch.setattr(
        generator,
        "configure",
        lambda config, **kwargs: configured.update(config=config, **kwargs),
    )
    monkeypatch.setattr(generator, "collect_structured_evidence", lambda: [])
    monkeypatch.setattr(
        pipeline,
        "extract_project_evidence",
        lambda *args, **kwargs: SimpleNamespace(
            index_path=tmp_path / "work/evidence-extraction/project-index.json",
            markdown_path=tmp_path / "work/evidence-extraction/extraction.md",
            status="materials_extracted",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "write_parameter_extraction_report",
        lambda *args, **kwargs: SimpleNamespace(
            json_path=tmp_path / "work/stage-gates/parameter-extraction-report.json",
            markdown_path=tmp_path / "work/stage-gates/parameter-extraction-report.md",
            status="parameter_extraction_reported",
        ),
    )
    monkeypatch.setattr(pipeline, "write_material_readiness", lambda *args: None)
    monkeypatch.setattr(pipeline, "append_run_history", lambda *args: None)

    result = pipeline.extract_project_materials(tmp_path)

    assert result.status == "materials_extracted"
    assert configured["config"].features["include_air_piping_pages"] is True
    assert configured["config"].features["include_accessory_pages"] is False
    assert configured["load_parameter_structured_evidence"] is False
    assert configured["load_formal_structured_evidence"] is False
    assert configured["load_summary_overrides"] is False


def test_curve_review_related_metrics_preserve_all_run_ramp_families() -> None:
    run_ramp = {
        family: {"max": index + 0.3, "average": index + 0.2, "min": index + 0.1}
        for index, family in enumerate(("hysteresis", "dead_band", "linearity"), 1)
    }

    related = pipeline._related_metrics(
        {
            "run_ramp": run_ramp,
            "Maximum Hysteresis + Dead Band": 99,
            "Average Hysteresis + Dead Band": 88,
        },
        "run_ramp",
    )

    assert related == {"run_ramp": run_ramp}
    assert "Maximum Hysteresis + Dead Band" not in related


@pytest.mark.parametrize("blank_setting", [None, False, True])
def test_generate_review_report_uses_business_profile_with_ratings_and_actions(
    tmp_path: Path, monkeypatch, blank_setting
) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[paths]
raw = "raw"
work = "work/shared_engine"
output = "output"
output_docx = "legacy.docx"
[presentation]
include_field_context_overview = false
term_replacements = { "Signature" = "动态性能测试" }
    """.strip(), encoding="utf-8")
    if blank_setting is not None:
        with (project / "project.toml").open("a", encoding="utf-8") as handle:
            handle.write(f"\nblank_conclusions = {str(blank_setting).lower()}\n")
    (project / "work/shared_engine").mkdir(parents=True)
    (project / "work/shared_engine/curve_review_manifest.json").write_text(
        json.dumps({"images": []}), encoding="utf-8"
    )
    (project / "work/shared_engine/codex_curve_interpretations.json").write_text(
        "[]", encoding="utf-8"
    )
    from offline_engine import generator
    configured = {}

    def fake_configure(config, **kwargs):
        configured["config"] = config
        configured.update(kwargs)

    def fake_render(config, _valves):
        config.output_docx.parent.mkdir(parents=True, exist_ok=True)
        config.output_docx.write_bytes(b"docx")
        config.work_dir.mkdir(parents=True, exist_ok=True)
        (config.work_dir / "analysis_summary.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(generator, "configure", fake_configure)
    monkeypatch.setattr(generator, "prepare_valve_data", lambda **_kwargs: [])
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", fake_render)
    monkeypatch.setattr(pipeline, "validate_report", lambda *args, **kwargs: {"ok": True})
    def fake_run_project(_project, *, config_override, **_kwargs):
        fake_configure(config_override)
        fake_render(config_override, [])
        manifest = config_override.work_dir / "run_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return pipeline.PipelineResult(config_override, manifest, "generated")

    monkeypatch.setattr(pipeline, "run_project", fake_run_project)

    result = generate_review_report(project, skip_pdf=True)

    variant = configured["config"]
    assert result.status == "review_generated"
    assert variant.output_docx == project / "output/review/DemoProject-待人工校验版-v1.docx"
    assert variant.manual_interpretations_file is None
    assert variant.diagnostic_summary_overrides_file is None
    assert variant.presentation == {
            "profile": "business_dashboard",
            "review_label": "待人工校验版",
            "report_period_label": "",
            "blank_ratings": False,
        "blank_conclusions": blank_setting is True,
        "blank_actions": False,
        "include_field_context_overview": False,
        "term_replacements": {"Signature": "动态性能测试"},
        "include_overview_judgement": False,
        "include_overview_priority": False,
        "include_retest_recommendations": False,
    }
    assert (project / "work/review/v1/diagnostic_snapshot.json").read_text(encoding="utf-8") == "[]"
    assert result.manifest_path.read_bytes() == (variant.work_dir / "run_manifest.json").read_bytes()
    history = json.loads((project / "work/run_history.jsonl").read_text())
    assert history["event"] == "review_generated"
    assert history["version"] == 1
    assert history["snapshot_sha256"] == hashlib.sha256(b"[]").hexdigest()
    assert history["pdf"] == ""
    assert "period" not in history


def test_generate_data_report_uses_pure_data_profile(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[report_periods]
active = "rolling"
rolling_label = "2026年7月22日至今"
[paths]
raw = "raw"
work = "work/shared_engine"
output = "output"
output_docx = "legacy.docx"
formal_diagnosis_run = "work/broken-formal"
        """.strip(),
        encoding="utf-8",
    )
    (project / "work/shared_engine").mkdir(parents=True)
    (project / "work/shared_engine/curve_review_manifest.json").write_text(
        json.dumps({"images": []}), encoding="utf-8"
    )
    (project / "work/shared_engine/codex_curve_interpretations.json").write_text(
        "[]", encoding="utf-8"
    )
    from offline_engine import generator
    configured = {}
    prepared = {}

    def fake_configure(config, **kwargs):
        configured["config"] = config
        configured.update(kwargs)

    def fake_render(config, _valves):
        config.output_docx.parent.mkdir(parents=True, exist_ok=True)
        config.output_docx.write_bytes(b"docx")
        config.work_dir.mkdir(parents=True, exist_ok=True)
        (config.work_dir / "analysis_summary.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(generator, "configure", fake_configure)
    def fake_prepare_valve_data(**kwargs):
        prepared.update(kwargs)
        return []

    monkeypatch.setattr(generator, "prepare_valve_data", fake_prepare_valve_data)
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", fake_render)
    monkeypatch.setattr(pipeline, "validate_report", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        __import__("offline_engine.experience", fromlist=["load_offline_experience"]),
        "load_offline_experience",
        lambda *_args: pytest.fail("data-only preparation must not import diagnosis feedback"),
    )
    monkeypatch.setattr(
        pipeline,
        "load_formal_diagnosis",
        lambda *_args, **_kwargs: pytest.fail("data-only report must not read formal diagnosis"),
    )
    monkeypatch.setattr(
        pipeline,
        "curve_review_readiness",
        lambda *_args, **_kwargs: pytest.fail("data-only report must not read curve diagnosis"),
    )

    result = pipeline.generate_data_report(project, skip_pdf=True)

    variant = configured["config"]
    assert result.status == "data_generated"
    assert variant.output_docx == (
        project
        / "output/data/DemoProject-2026年7月22日至今-纯数据版-v1.docx"
    )
    assert variant.report_title == "Masoneilan ValVue3 离线检测数据报告"
    assert variant.presentation == {
        "profile": "business_dashboard",
        "review_label": "纯数据版",
        "report_period_label": "2026年7月22日至今",
        "data_only": True,
        "blank_ratings": True,
        "blank_conclusions": True,
        "blank_actions": True,
        "include_field_context_overview": False,
        "include_overview_judgement": False,
        "include_overview_priority": False,
        "include_retest_recommendations": False,
    }
    assert prepared == {
        "curve_review_required_folders": None,
    }
    assert not (
        project
        / "work/data/2026年7月22日至今/v1/diagnostic_snapshot.json"
    ).exists()
    assert result.manifest_path.read_bytes() == (variant.work_dir / "run_manifest.json").read_bytes()
    assert "formal_diagnosis" not in json.loads(result.manifest_path.read_text())
    history = json.loads((project / "work/run_history.jsonl").read_text())
    assert history["event"] == "data_generated"
    assert history["period"] == "2026年7月22日至今"
    assert "snapshot_sha256" not in history


def test_diagnostic_report_requires_a_formal_diagnosis_binding(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        '[project]\ncustomer="Demo"\nreport_title="Report"\n'
        'report_date="2026-09-05"\n'
        '[paths]\nraw="raw"\nwork="work"\noutput_docx="report.docx"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        pipeline.FormalDiagnosisError,
        match="formal diagnosis.*required",
    ):
        pipeline.run_project(tmp_path, skip_pdf=True)

    assert not (tmp_path / "report.docx").exists()


def test_data_only_report_rejects_curve_review_finalization(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        '[project]\ncustomer="Demo"\nreport_title="Data"\n'
        'report_date="2026-09-05"\n'
        '[paths]\nraw="raw"\nwork="work"\noutput_docx="data.docx"\n'
        '[presentation]\ndata_only=true\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="data-only.*cannot finalize"):
        pipeline.run_project(
            tmp_path,
            skip_pdf=True,
            finalize_curve_review=True,
        )


@pytest.mark.parametrize("generate", [generate_review_report, pipeline.generate_data_report])
def test_report_waiting_for_curve_review_does_not_archive(tmp_path: Path, monkeypatch, generate) -> None:
    (tmp_path / "project.toml").write_text(
        '[project]\ncustomer="Demo"\nreport_title="Report"\nreport_date="2026-09-05"\n'
        '[paths]\noutput_docx="report.docx"\n'
    )
    pending = pipeline.PipelineResult(
        config=load_project(tmp_path),
        manifest_path=tmp_path / "work/run_manifest.json",
        status="curve_review_required",
        missing_image_keys=("curve-one",),
    )
    monkeypatch.setattr(pipeline, "run_project", lambda *args, **kwargs: pending)

    assert generate(tmp_path, skip_pdf=True) is pending
    assert not (tmp_path / "work/run_history.jsonl").exists()
    assert not list(tmp_path.rglob("diagnostic_snapshot.json"))


def test_prepare_project_ocr_writes_project_manifest(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
""".strip(), encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "prepare_project_pdf_cache",
        lambda project: {"documents": 0, "cache_hits": 0, "native_pages": 0, "ocr_pages": 0},
    )

    result = prepare_project_ocr(tmp_path)

    assert result.status == "ocr_prepared"
    assert result.manifest_path == tmp_path / "work/ocr/manifest.json"
    assert result.manifest_path.is_file()
    assert not (tmp_path / "work/shared_engine/spec_ocr_cache.json").exists()


def test_business_report_uses_latest_ingested_review_and_business_profile(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw/FV-101").mkdir(parents=True)
    (project / "work/shared_engine").mkdir(parents=True)
    (project / "work/shared_engine/curve_review_manifest.json").write_text(
        json.dumps({"images": []}), encoding="utf-8"
    )
    (project / "work/shared_engine/codex_curve_interpretations.json").write_text(
        "[]", encoding="utf-8"
    )
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
""".strip(), encoding="utf-8")
    source = project / "output/review/DemoProject-人工校验版-v1.docx"
    returned = project / "incoming/returned.docx"
    from test_review_workflow import _write_review

    _write_review(source, rating="", conclusion="", action="")
    _write_review(returned, rating="3级", conclusion="存在迟滞。", action="窗口期复测。")
    _register_review_source(project, source)
    imported = ingest_review_report(project, returned)
    (project / "work/shared_engine/manual_curve_interpretations.json").write_text(
        json.dumps(
            {
                "interpretations": [
                    {
                        "image_key": "old-curve",
                        "customer_text": "旧人工逐图判读不得混入本次商务版。",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (imported.report_overrides_json).write_text(
        json.dumps(
            {
                "overall_conclusion": "人工总体结论。",
                "recommendations": ["人工建议。"],
            }
        ),
        encoding="utf-8",
    )

    from offline_engine import generator
    configured = {}

    def fake_configure(config, **kwargs):
        configured["config"] = config
        configured.update(kwargs)

    def fake_render(config, _valves):
        config.output_docx.parent.mkdir(parents=True, exist_ok=True)
        config.output_docx.write_bytes(b"docx")
        config.work_dir.mkdir(parents=True, exist_ok=True)
        (config.work_dir / "analysis_summary.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(generator, "configure", fake_configure)
    monkeypatch.setattr(generator, "prepare_valve_data", lambda **_kwargs: [])
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", fake_render)
    monkeypatch.setattr(pipeline, "validate_report", lambda *args, **kwargs: {"ok": True})

    monkeypatch.setattr(pipeline, "_validate_review_formal_binding", lambda *_args: None)

    def fake_business_run(_project, *, config_override, **_kwargs):
        fake_configure(config_override)
        fake_render(config_override, [])
        manifest = config_override.work_dir / "run_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return pipeline.PipelineResult(config_override, manifest, "generated")

    monkeypatch.setattr(pipeline, "run_project", fake_business_run)

    result = generate_business_report(project, skip_pdf=True)

    variant = configured["config"]
    assert result.status == "business_generated"
    assert variant.output_docx == project / "output/business/DemoProject-最终商务版-v1.docx"
    assert variant.presentation["profile"] == "business_dashboard"
    assert variant.presentation["overall_conclusion_override"] == "人工总体结论。"
    assert variant.presentation["recommendation_overrides"] == ["人工建议。"]
    assert variant.presentation["include_field_context_overview"] is False
    assert variant.presentation["include_overview_judgement"] is False
    assert variant.presentation["include_overview_priority"] is False
    assert variant.presentation["include_retest_recommendations"] is False
    assert variant.diagnostic_summary_overrides_file == imported.overrides_json
    assert variant.manual_interpretations_file is None
    assert (project / "work/run_history.jsonl").read_text(encoding="utf-8").count("business_generated") == 1

    (imported.report_overrides_json).write_text(
        json.dumps(
            {
                "overall_conclusion": {"invalid": "object"},
                "recommendations": ["人工建议。", 1],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="overall_conclusion must be non-empty text"):
        generate_business_report(project, skip_pdf=True)

    (imported.report_overrides_json).write_text(
        json.dumps(
            {
                "overall_conclusion": "人工总体结论。",
                "recommendations": ["人工建议。", {"invalid": "object"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="recommendations must be a non-empty list of text"):
        generate_business_report(project, skip_pdf=True)


def test_business_report_rejects_unbound_partial_human_fields(tmp_path: Path) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw/FV-101").mkdir(parents=True)
    (project / "work/review/v1").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
""".strip(), encoding="utf-8")
    (project / "work/review/v1/change_list.json").write_text(
        json.dumps({"review_version": 1, "overrides": {"FV-101": {"rating": 2}}}),
        encoding="utf-8",
    )
    (project / "work/review/v1/diagnostic_overrides.json").write_text(
        json.dumps({"FV-101": {"rating": 2}}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="not bound.*formal diagnosis"):
        generate_business_report(project, skip_pdf=True)


def test_ingested_explicit_diagnostic_clear_is_rejected_before_business_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw/FV-101").mkdir(parents=True)
    (project / "work/shared_engine/formal-run").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-09-05"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
formal_diagnosis_run = "work/shared_engine/formal-run"
""".strip(),
        encoding="utf-8",
    )
    (project / "work/shared_engine/analysis_summary.json").write_text(
        json.dumps([{"folder": "FV-101"}]),
        encoding="utf-8",
    )
    (project / "work/shared_engine/curve_review_manifest.json").write_text(
        json.dumps({"images": []}),
        encoding="utf-8",
    )
    source = project / "output/review/DemoProject-人工校验版-v1.docx"
    returned = project / "incoming/returned.docx"
    from test_review_workflow import _write_review

    _write_review(
        source,
        rating="2级",
        conclusion="冻结评级结论。",
        action="冻结建议。",
    )
    _write_review(returned, rating="2级", conclusion="", action="")
    _register_review_source(project, source)
    imported = ingest_review_report(project, returned)
    overrides = json.loads(imported.overrides_json.read_text(encoding="utf-8"))
    assert overrides["FV-101"]["rating_conclusion"] == ""
    assert overrides["FV-101"]["actions"] == []

    formal = FormalDiagnosis(
        curve_interpretations=(),
        summary_overrides={
            "FV-101": {
                "rating": 2,
                "rating_conclusion": "冻结评级结论。",
                "main_issue": "冻结问题",
                "issues": [],
                "action": "冻结建议。",
                "actions": ["冻结建议。"],
                "overall_assessment": "冻结综合诊断结论。",
            }
        },
    )
    monkeypatch.setattr(pipeline, "load_formal_diagnosis", lambda *_args, **_kwargs: formal)
    monkeypatch.setattr(pipeline, "_validate_review_formal_binding", lambda *_args: None)
    monkeypatch.setattr(pipeline, "_validate_diagnostic_overlay_binding", lambda *_args: None)

    with pytest.raises(ValueError, match="(action|rating_conclusion) must be non-empty text"):
        generate_business_report(project, skip_pdf=True)


def test_business_report_blocks_unmapped_human_changes(tmp_path: Path) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw/FV-101").mkdir(parents=True)
    (project / "work/review/v1").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-23"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
""".strip(), encoding="utf-8")
    override = {
        "FV-101": {
            "rating": 2,
            "rating_conclusion": "轻微迟滞。",
            "action": "复测。",
            "actions": ["复测。"],
        }
    }
    (project / "work/review/v1/change_list.json").write_text(
        json.dumps(
            {
                "review_version": 1,
                "overrides": override,
                "unresolved_changes": [{"after": "供气压力改为 420 kPa"}],
            }
        ), encoding="utf-8"
    )
    (project / "work/review/v1/diagnostic_overrides.json").write_text(
        json.dumps(override), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="unmapped human changes"):
        generate_business_report(project, skip_pdf=True)


def test_review_import_blocker_keeps_word_comments_out_of_business_report() -> None:
    assert "comments" in pipeline._review_import_blocker(
        {"unresolved_changes": [], "comments": ["请核对此处"]}
    )


def test_input_tree_hash_changes_when_file_content_changes(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    source = raw / "FV-1/data.bin"
    source.parent.mkdir()
    source.write_bytes(b"first")
    first = pipeline._input_summary(raw)["tree_sha256"]

    source.write_bytes(b"second")
    second = pipeline._input_summary(raw)["tree_sha256"]

    assert first != second


def test_convert_docx_to_pdf_writes_internal_check_file_not_delivery_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "ValVue3 Offline Diagnostic Report"
report_date = "2026-07-11"

[paths]
raw = "raw"
specs = "specs"
inbound = "inbound"
work = "work"
output = "output"
output_docx = "Demo.docx"
output_pdf = "Customer-Report.pdf"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)
    config.output_dir.mkdir()
    config.output_docx.write_bytes(b"docx")
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: "/usr/bin/libreoffice")

    def fake_run(command, **kwargs):
        output_dir = Path(command[command.index("--outdir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Demo.pdf").write_bytes(b"pdf")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    result = convert_docx_to_pdf(config)

    expected = config.work_dir / "pdf-checks/Demo.libreoffice-check.pdf"
    assert result == expected
    assert expected.read_bytes() == b"pdf"
    assert not config.output_pdf.exists()
    assert not (config.output_dir / "Demo.pdf").exists()


def test_convert_docx_to_pdf_requires_libreoffice(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)
    config.output_dir.mkdir()
    config.output_docx.write_bytes(b"docx")
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="LibreOffice"):
        convert_docx_to_pdf(config)


def _write_layout_pdf(
    path: Path,
    *,
    creator: str,
    blank_page: bool = False,
    data_heading: str = "关键测试数据",
) -> None:
    document = fitz.open()
    cover = document.new_page(width=595.28, height=841.89)
    cover.insert_text((72, 100), "ValVue3 Offline Diagnostic Report")
    overview = document.new_page(width=595.28, height=841.89)
    font_file = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    overview.insert_font(fontname="cjk", fontfile=font_file)
    overview.insert_text(
        (72, 100),
        f"FV-101\n设备概览\n{data_heading}\n运行与 DTM 信息",
        fontname="cjk",
    )
    final = document.new_page(width=595.28, height=841.89)
    if not blank_page:
        final.insert_font(fontname="cjk", fontfile=font_file)
        final.insert_text(
            (72, 100),
            "FV-101 图形判读",
            fontname="cjk",
        )
    document.set_metadata({"creator": creator})
    document.save(path)
    document.close()


def test_wps_layout_inspection_requires_wps_creator_and_builds_contact_sheet(
    tmp_path: Path,
) -> None:
    wps_pdf = tmp_path / "report.pdf"
    contact_sheet = tmp_path / "contact-sheet.png"
    _write_layout_pdf(wps_pdf, creator="WPS 文字")

    result = pipeline.inspect_wps_pdf_layout(
        wps_pdf,
        contact_sheet=contact_sheet,
    )

    assert result["ok"] is True
    assert result["creator"] == "WPS 文字"
    assert result["page_count"] == 3
    assert result["a4_portrait_pages"] == 3
    assert result["blank_pages"] == []
    assert result["incomplete_device_overview_pages"] == []
    assert result["visual_review_required"] is True
    assert contact_sheet.is_file()


def test_wps_layout_inspection_rejects_libreoffice_and_blank_pages(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "libreoffice.pdf"
    _write_layout_pdf(pdf, creator="LibreOffice 24.2", blank_page=True)

    result = pipeline.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=tmp_path / "contact-sheet.png",
    )

    assert result["ok"] is False
    assert result["creator_is_wps"] is False
    assert result["blank_pages"] == [3]
    assert any("WPS" in failure for failure in result["failures"])
    assert any("blank" in failure.lower() for failure in result["failures"])


def test_wps_layout_inspection_accepts_historical_diagnostic_data_heading(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "historical-wps.pdf"
    _write_layout_pdf(
        pdf,
        creator="WPS 文字",
        data_heading="关键诊断数据",
    )

    result = pipeline.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=tmp_path / "contact-sheet.png",
    )

    assert result["incomplete_device_overview_pages"] == []
    assert result["ok"] is True


def test_wps_layout_inspection_rejects_device_overview_continuation_page(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "split-device-overview.pdf"
    contact_sheet = tmp_path / "contact-sheet.png"
    font_file = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    document = fitz.open()
    cover = document.new_page(width=595.28, height=841.89)
    cover.insert_text((72, 100), "ValVue3 Offline Diagnostic Report")
    overview = document.new_page(width=595.28, height=841.89)
    overview.insert_font(fontname="cjk", fontfile=font_file)
    overview.insert_text(
        (72, 100),
        (
            "FV-101\n诊断结论\n设备概览\n主要问题与建议行动\n"
            "关键诊断数据\n运行与 DTM 信息\n运行与故障摘要\n"
            "历史故障：Actuator Error；Low Air Supply Warning；Position"
        ),
        fontname="cjk",
    )
    continuation = document.new_page(width=595.28, height=841.89)
    continuation.insert_font(fontname="cjk", fontfile=font_file)
    continuation.insert_text(
        (72, 100),
        "Error；Marginal Power",
        fontname="cjk",
    )
    curve = document.new_page(width=595.28, height=841.89)
    curve.insert_font(fontname="cjk", fontfile=font_file)
    curve.insert_text(
        (72, 100),
        "FV-101 | 曲线证据与图形判读",
        fontname="cjk",
    )
    document.set_metadata({"creator": "WPS 文字"})
    document.save(pdf)
    document.close()

    result = pipeline.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=contact_sheet,
    )

    assert "device_overview_continuation_pages" in result
    assert result["device_overview_continuation_pages"] == [
        {"overview_page": 2, "continuation_page": 3}
    ]
    assert len(result["device_overview_contact_sheets"]) == 1
    assert Path(result["device_overview_contact_sheets"][0]).is_file()
    assert result["ok"] is False
    assert any(
        "continuation" in failure.lower()
        for failure in result["failures"]
    )


def test_wps_layout_inspection_rejects_orphan_text_page(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "orphan-text.pdf"
    font_file = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    document = fitz.open()
    cover = document.new_page(width=595.28, height=841.89)
    cover.insert_text((72, 100), "ValVue3 Offline Diagnostic Report")
    conclusion = document.new_page(width=595.28, height=841.89)
    conclusion.insert_font(fontname="cjk", fontfile=font_file)
    conclusion.insert_text(
        (72, 100),
        "总体结论与检修建议\n后续复测、维护和检修建议",
        fontname="cjk",
    )
    orphan = document.new_page(width=595.28, height=841.89)
    orphan.insert_font(fontname="cjk", fontfile=font_file)
    orphan.insert_text(
        (72, 100),
        "• 2级阀门以持续观察和现场趋势复核为主。",
        fontname="cjk",
    )
    device = document.new_page(width=595.28, height=841.89)
    device.insert_font(fontname="cjk", fontfile=font_file)
    device.insert_text(
        (72, 100),
        "FV-101\n诊断结论\n设备概览\n关键诊断数据\n运行与 DTM 信息",
        fontname="cjk",
    )
    document.set_metadata({"creator": "WPS 文字"})
    document.save(pdf)
    document.close()

    result = pipeline.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=tmp_path / "contact-sheet.png",
    )

    assert "orphan_text_pages" in result
    assert result["orphan_text_pages"] == [3]
    assert result["ok"] is False
    assert any("orphan" in failure.lower() for failure in result["failures"])


def test_wps_layout_inspection_accepts_complete_overall_judgment_page(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "overall-judgment.pdf"
    font_file = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    document = fitz.open()
    cover = document.new_page(width=595.28, height=841.89)
    cover.insert_text((72, 100), "ValVue3 Offline Diagnostic Report")
    overview = document.new_page(width=595.28, height=841.89)
    overview.insert_font(fontname="cjk", fontfile=font_file)
    overview.insert_text(
        (72, 100),
        "整体判断\n2级｜6台\n3级｜64台\n4级｜2台\n首要关注\nFV-101：立即安排检修。",
        fontname="cjk",
    )
    device = document.new_page(width=595.28, height=841.89)
    device.insert_font(fontname="cjk", fontfile=font_file)
    device.insert_text(
        (72, 100),
        "FV-101\n诊断结论\n设备概览\n关键诊断数据\n运行与 DTM 信息",
        fontname="cjk",
    )
    document.set_metadata({"creator": "WPS 文字"})
    document.save(pdf)
    document.close()

    result = pipeline.inspect_wps_pdf_layout(
        pdf,
        contact_sheet=tmp_path / "contact-sheet.png",
    )

    assert result["orphan_text_pages"] == []
    assert result["ok"] is True


def test_validate_report_accepts_explicit_internal_check_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path
    (project / "raw").mkdir()
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "work"
output = "output"
output_docx = "Demo.docx"
output_pdf = "Demo.pdf"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(project)
    config.work_dir.mkdir(parents=True)
    internal_pdf = config.work_dir / "pdf-checks/Demo.libreoffice-check.pdf"
    captured: dict[str, object] = {}

    def fake_check(project, docx, summary, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(pipeline, "check_required_information", fake_check)

    validation = pipeline.validate_report(
        config,
        pdf_path=internal_pdf,
    )

    assert validation["ok"] is True
    assert captured["pdf"] == internal_pdf
    assert captured["config"] is config


def test_validate_report_can_explicitly_run_deep_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    config = load_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True}),
            stderr="",
        )

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    pipeline.validate_report(
        config,
        require_pdf=False,
        information_only=False,
    )

    assert "--information-only" not in captured["command"]


def test_validate_report_keeps_selected_period_and_in_memory_variant(tmp_path: Path) -> None:
    (tmp_path / "project.toml").write_text('''
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-09-27"
[paths]
output_docx = "report.docx"
[report_periods]
active = "a"
a_label = "Batch A"
b_label = "Batch B"
[report_period_overrides.b.presentation]
include_overview_judgement = true
''', encoding="utf-8")
    config = load_project(tmp_path, report_period="b")
    config.work_dir.mkdir(parents=True)
    config.output_dir.mkdir(parents=True)
    summary = config.work_dir / "analysis_summary.json"
    summary.write_text(json.dumps([{"folder": "VALVE-001", "images": []}]))
    document = Document()
    document.add_paragraph("VALVE-001")
    document.add_heading("整体判断", level=1)
    document.save(config.output_docx)

    assert pipeline.validate_report(config, require_pdf=False)["ok"] is True
    wrong_period = load_project(tmp_path, report_period="a")
    assert pipeline.validate_report(wrong_period, require_pdf=False, raise_on_failure=False)["ok"] is False

    summary.write_text(json.dumps([{"folder": "VALVE-001", "images": [], "issues": ["需要检修"]}]))
    data_variant = replace(config, presentation={**config.presentation, "data_only": True})
    assert pipeline.validate_report(data_variant, require_pdf=False)["ok"] is True
    assert pipeline.validate_report(config, require_pdf=False, raise_on_failure=False)["ok"] is False


def test_deep_report_validation_keeps_selected_period(tmp_path: Path, monkeypatch) -> None:
    config = SimpleNamespace(
        project_dir=tmp_path, work_dir=tmp_path, output_docx=tmp_path / "report.docx",
        report_period_key="batch_b", features={},
    )
    command = []

    def run(args, **kwargs):
        command.extend(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    pipeline.validate_report(config, require_pdf=False, information_only=False)
    assert command[command.index("--report-period") + 1] == "batch_b"


def test_check_report_information_writes_one_compact_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-08-28"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    docx = tmp_path / "result.docx"
    docx.write_bytes(b"placeholder")
    accepted = {
        "ok": True,
        "status": "information_complete",
        "counts": {
            "valves": {"expected": 1, "confirmed": 1},
            "fields": {"expected": 28, "confirmed": 28},
            "curves": {"expected": 5, "confirmed": 5},
            "technical_pages": {"expected": 2, "confirmed": 2},
        },
        "issues": [],
    }
    monkeypatch.setattr(
        pipeline,
        "validate_report",
        lambda *args, **kwargs: accepted,
    )

    result = check_report_information(tmp_path, docx=docx)

    assert result.status == "information_complete"
    assert result.report_path == docx.resolve()
    assert result.manifest_path == (
        tmp_path / "work/information-acceptance/result.json"
    )
    assert json.loads(result.manifest_path.read_text(encoding="utf-8")) == accepted


def test_validate_report_skips_spec_match_requirement_when_specs_are_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path
    (project / "raw").mkdir()
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "work"
output = "output"
output_docx = "Demo.docx"
output_pdf = "Demo.pdf"
[features]
include_specifications = false
""".strip(),
        encoding="utf-8",
    )
    config = load_project(project)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True}),
            stderr="",
        )

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    pipeline.validate_report(config, require_pdf=False, information_only=False)

    command = captured["command"]
    assert isinstance(command, list)
    assert "--allow-no-spec-match" in command


def test_wps_delivery_requires_visual_review_before_final_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "work/shared_engine"
output = "output"
output_docx = "legacy.docx"
output_pdf = "legacy.pdf"
""".strip(),
        encoding="utf-8",
    )
    docx = project / "output/review/DemoProject-人工校验版-v1.docx"
    pdf = docx.with_suffix(".pdf")
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"docx")
    _write_layout_pdf(pdf, creator="WPS 文字")
    monkeypatch.setattr(
        pipeline,
        "validate_report",
        lambda *args, **kwargs: {"ok": True, "failures": []},
    )

    pending = pipeline.validate_wps_delivery(
        project,
        docx=docx,
        wps_pdf=pdf,
    )

    assert pending.status == "wps_layout_review_required"
    pending_manifest = json.loads(
        pending.manifest_path.read_text(encoding="utf-8")
    )
    assert pending_manifest["status"] == "wps_layout_review_required"
    assert pending_manifest["automatic_layout"]["ok"] is True
    assert Path(
        pending_manifest["automatic_layout"]["contact_sheet"]
    ).is_file()
    assert pending_manifest["visual_review"]["approved"] is False

    with pytest.raises(RuntimeError, match="review note"):
        pipeline.validate_wps_delivery(
            project,
            docx=docx,
            wps_pdf=pdf,
            approve_visual=True,
        )

    approved = pipeline.validate_wps_delivery(
        project,
        docx=docx,
        wps_pdf=pdf,
        approve_visual=True,
        visual_review_note="已检查封面、总览、首末阀门、曲线和规格书代表页，版面整洁。",
    )

    assert approved.status == "wps_delivery_validated"
    approved_manifest = json.loads(
        approved.manifest_path.read_text(encoding="utf-8")
    )
    assert approved_manifest["visual_review"]["approved"] is True
    assert approved_manifest["delivery"]["pdf"] == str(pdf.resolve())
    assert len(approved_manifest["delivery"]["pdf_sha256"]) == 64


def test_prepare_curve_review_renders_manifest_in_configured_work_dir(tmp_path: Path, monkeypatch) -> None:
    raw_valve_dir = tmp_path / "raw/FV-101"
    raw_valve_dir.mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "custom-work"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )

    from offline_engine import generator

    def render_review_image(folder: Path, *, include_observation: bool = True, trusted_device_id: str = ""):
        assert folder == raw_valve_dir
        assert include_observation is False
        return [
            {
                "path": str(tmp_path / "custom-work/report_images/FV-101/小信号过冲欠调.png"),
                "source": "seq.pdf",
                "page": "3",
                "bbox": "1.0,2.0,3.0,4.0",
                "test_key": "run_step",
            }
        ]

    monkeypatch.setattr(generator, "render_images_for_valve", render_review_image)
    result = pipeline.prepare_curve_review(tmp_path)

    assert result.status == "curve_review_required"
    assert result.manifest_path == tmp_path / "custom-work/curve_review_manifest.json"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "curve_review_required"
    image = manifest["images"][0]
    assert image["image_key"].startswith("FV-101:run_step:")
    assert image["valve_tag"] == "FV-101"
    assert image["image_path"].endswith("小信号过冲欠调.png")
    assert image["related_metrics"] == {"Average Error": None, "T86": None, "T63": None, "Td": None, "Overshoot": None, "step_count": 0}
    assert "applicable_common_experience_rules" not in image
    assert not (tmp_path / "output/Demo.docx").exists()
    assert not (tmp_path / "output/Demo.pdf").exists()


def test_prepare_curve_review_applies_only_valves_before_rendering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for tag in ("FV-KEEP", "FV-SKIP"):
        (tmp_path / "raw" / tag).mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
[report_layout]
only_valves = ["FV-KEEP"]
""".strip(),
        encoding="utf-8",
    )

    from offline_engine import generator

    rendered: list[str] = []
    valves = [
        generator.ValveReport(folder="FV-KEEP"),
        generator.ValveReport(folder="FV-SKIP"),
    ]

    def collect_review_valves(*, render_assets: bool = True):
        assert render_assets is False
        return valves

    def render_review_assets(valve):
        rendered.append(valve.folder)
        valve.images = [{
            "path": str(tmp_path / "work/report_images" / valve.folder / "curve.png"),
            "source": "seq.pdf",
            "page": "3",
            "bbox": "1,2,3,4",
            "test_key": "run_step",
        }]

    monkeypatch.setattr(generator, "collect_curve_review_valves", collect_review_valves)
    monkeypatch.setattr(generator, "render_curve_review_assets", render_review_assets)

    result = pipeline.prepare_curve_review(tmp_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert rendered == ["FV-KEEP"]
    assert [item["valve_tag"] for item in manifest["images"]] == ["FV-KEEP"]


def test_prepare_curve_review_does_not_load_project_curve_observations_or_manual_interpretations(
    tmp_path: Path, monkeypatch
) -> None:
    raw_valve_dir = tmp_path / "raw/FV-102"
    raw_valve_dir.mkdir(parents=True)
    (tmp_path / "experience").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-11"
[paths]
raw = "raw"
work = "review-work"
manual_interpretations = "experience/manual-sentinel.json"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "curve_observations.json").write_text('{"sentinel": "MUST NOT APPEAR"}', encoding="utf-8")
    (tmp_path / "experience/manual-sentinel.json").write_text('{"sentinel": "MUST NOT APPEAR"}', encoding="utf-8")
    (tmp_path / "experience/project_experience.json").write_text(
        '{"rules": [{"corrected_interpretation": "MUST NOT APPEAR"}]}', encoding="utf-8"
    )

    from offline_engine import generator

    original_load = generator._load_project_json

    def reject_curve_observation_load(name: str, default):
        if name == "curve_observations.json":
            raise AssertionError("curve observations must not be loaded during review preparation")
        return original_load(name, default)

    monkeypatch.setattr(generator, "_load_project_json", reject_curve_observation_load)
    monkeypatch.setattr(generator, "collect_valves", lambda: (_ for _ in ()).throw(AssertionError("rating collection must not run")))
    monkeypatch.setattr(
        __import__("offline_engine.experience", fromlist=["load_offline_experience"]),
        "load_offline_experience",
        lambda path: (_ for _ in ()).throw(AssertionError(f"project experience must not be loaded: {path}")),
    )

    def render_review_image(folder: Path, *, include_observation: bool = True, trusted_device_id: str = ""):
        assert folder == raw_valve_dir
        assert include_observation is False
        return [
            {
                "path": str(tmp_path / "review-work/chart.png"),
                "source": "seq.pdf",
                "page": "1",
                "test_key": "run_step",
            }
        ]

    monkeypatch.setattr(generator, "render_images_for_valve", render_review_image)

    manifest = json.loads(pipeline.prepare_curve_review(tmp_path).manifest_path.read_text(encoding="utf-8"))

    assert "MUST NOT APPEAR" not in json.dumps(manifest, ensure_ascii=False)
    assert not (tmp_path / "output/Demo.docx").exists()
    assert not (tmp_path / "output/Demo.pdf").exists()


def test_curve_review_finalize_status_uses_standard_work_files_and_blocks_missing(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [
            {"image_key": "stable-a", "test_key": "run_step"},
            {"image_key": "stable-b", "test_key": "run_ramp"},
        ]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "正常", "diagnosis": "正常",
        "rating": 1, "action": "维持复测", "customer_text": "未见异常", "confidence": "high",
        "experience_rule_ids": []
    }]), encoding="utf-8")

    result = curve_review_finalize_status(tmp_path)

    assert result.status == "curve_review_required"
    assert result.missing_image_keys == ("stable-b",)


def test_run_project_blocks_generator_when_curve_review_manifest_is_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [
            {"image_key": "stable-a", "test_key": "run_step"},
            {"image_key": "stable-b", "test_key": "run_ramp"},
        ]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "正常", "diagnosis": "正常",
        "rating": 1, "action": "维持复测", "customer_text": "未见异常", "confidence": "high",
        "experience_rule_ids": []
    }]), encoding="utf-8")
    from offline_engine import generator

    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda: (_ for _ in ()).throw(AssertionError("valve preparation must not run before curve review is complete")),
    )

    with pytest.raises(pipeline.FormalDiagnosisError, match="binding is required"):
        run_project(tmp_path, skip_pdf=True)


def test_cli_rejects_diagnostic_report_without_formal_binding(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [
            {"image_key": "stable-a", "test_key": "run_step"},
            {"image_key": "stable-b", "test_key": "run_ramp"},
        ]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "正常", "diagnosis": "正常",
        "rating": 1, "action": "维持复测", "customer_text": "未见异常", "confidence": "high",
        "experience_rule_ids": []
    }]), encoding="utf-8")
    pipeline.prepare_project_materials(tmp_path)
    pipeline.approve_project_materials(
        tmp_path,
        approval_text="资料确认，可以进入下一阶段。",
    )
    evidence_output = tmp_path / "work/evidence-extraction"
    evidence_output.mkdir(parents=True)
    (evidence_output / "project-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "extractor_version": EXTRACTOR_VERSION,
                "valves": [],
            }
        ),
        encoding="utf-8",
    )
    write_parameter_extraction_report(load_project(tmp_path))
    approve_parameter_extraction(
        load_project(tmp_path),
        approval_text="参数确认，可以进入逐图观察。",
    )
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"

    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--skip-pdf"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "formal diagnosis binding is required" in result.stderr


def test_cli_blocks_curve_review_until_stage_two_parameters_are_approved(
    tmp_path: Path,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    pipeline.prepare_project_materials(tmp_path)
    pipeline.approve_project_materials(
        tmp_path,
        approval_text="资料确认，可以进入下一阶段。",
    )
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"

    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--prepare-curve-review"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Stage 2 parameter extraction approval is required" in result.stderr


def test_finalize_curve_review_loads_validated_interpretations_before_generating(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [{"image_key": "stable-a", "test_key": "run_step"}]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "曲线响应连续。", "diagnosis": "未见明显异常。",
        "rating": 1, "action": "维持复测", "customer_text": "曲线响应连续，未见明显异常。", "confidence": "high",
        "experience_rule_ids": ["rule-internal"]
    }]), encoding="utf-8")
    from offline_engine import generator
    configured: dict[str, object] = {}
    validated: dict[str, object] = {}
    readiness_calls = 0
    original_readiness = pipeline.curve_review_readiness

    def count_readiness_calls(*args, **kwargs):
        nonlocal readiness_calls
        readiness_calls += 1
        return original_readiness(*args, **kwargs)

    monkeypatch.setattr(pipeline, "curve_review_readiness", count_readiness_calls)
    monkeypatch.setattr(generator, "configure", lambda *_args, **kwargs: configured.update(kwargs))
    monkeypatch.setattr(generator, "prepare_valve_data", lambda **_kwargs: [])
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", lambda *_args: None)
    monkeypatch.setattr(
        pipeline,
        "validate_report",
        lambda *_args, **kwargs: validated.update(kwargs) or {"ok": True},
    )

    with pytest.raises(pipeline.FormalDiagnosisError, match="binding is required"):
        run_project(tmp_path, finalize_curve_review=True, skip_pdf=True)

    assert configured == {}
    assert validated == {}
    assert readiness_calls == 0


def test_standard_generation_prefers_configured_frozen_formal_diagnosis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work/formal-run").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
formal_diagnosis_run = "work/formal-run"
[report_layout]
only_valves = ["FV-1"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "work/curve_review_manifest.json").write_text(
        json.dumps({"images": [{"image_key": "stable-a", "test_key": "run_step"}]}),
        encoding="utf-8",
    )
    (tmp_path / "work/analysis_summary.json").write_text(
        json.dumps([{"folder": "FV-1", "report_pages": []}]),
        encoding="utf-8",
    )
    (tmp_path / "work/codex_curve_interpretations.json").write_text(
        json.dumps([{
            "image_key": "stable-a",
            "test_key": "run_step",
            "observation": "旧观察",
            "diagnosis": "旧判断",
            "rating": 1,
            "action": "旧建议",
            "customer_text": "旧逐图判读",
            "confidence": "high",
            "experience_rule_ids": [],
        }]),
        encoding="utf-8",
    )
    formal_curve = CurveInterpretation(
        image_key="stable-a",
        test_key="run_step",
        observation="新观察",
        diagnosis="新判断",
        rating=3,
        action="新建议",
        customer_text="新单阀诊断逐图判读",
        confidence="high",
        experience_rule_ids=(),
    )
    formal = FormalDiagnosis(
        curve_interpretations=(formal_curve,),
        summary_overrides={
            "FV-1": {
                "rating": 3,
                "rating_conclusion": "冻结评级结论。",
                "main_issue": "新整阀结论",
                "issues": ["冻结问题。"],
                "action": "冻结建议。",
                "actions": ["冻结建议。"],
                "overall_assessment": "冻结综合诊断结论。",
                "health_rating_detail_mode": "summary_only",
            }
        },
        algorithm_audit_statuses={"FV-1": "available"},
        algorithm_audit_snapshots={
            "FV-1": {
                "data_rating": 2,
                "visual_rating": 3,
                "algorithm_rating": 3,
                "rating_inconsistency_reason": None,
                "rating_evidence": {"source": "frozen audit"},
            }
        },
    )
    from offline_engine import generator
    configured: dict[str, object] = {}
    prepared: dict[str, object] = {}
    rendered: list[generator.ValveReport] = []

    monkeypatch.setattr(pipeline, "load_formal_diagnosis", lambda *_args, **_kwargs: formal)
    def fake_configure(*_args, **kwargs):
        configured.update(kwargs)
        generator.DIAGNOSTIC_SUMMARY_OVERRIDES = {
            tag: dict(summary)
            for tag, summary in kwargs["formal_summary_overrides"].items()
        }

    monkeypatch.setattr(generator, "configure", fake_configure)
    def fake_prepare(**kwargs):
        prepared.update(kwargs)
        return [generator.ValveReport(folder="FV-1")]

    monkeypatch.setattr(generator, "prepare_valve_data", fake_prepare)
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        pipeline,
        "_render_prepared_report",
        lambda _config, valves: rendered.extend(valves),
    )
    monkeypatch.setattr(pipeline, "validate_report", lambda *_args, **_kwargs: {"ok": True})

    result = run_project(tmp_path, skip_pdf=True)

    assert result.status == "generated"
    assert configured["curve_interpretations"][0].customer_text == "新单阀诊断逐图判读"
    assert configured["formal_summary_overrides"] == {
        "FV-1": formal.summary_overrides["FV-1"]
    }
    assert rendered[0].algorithm_rating == 3
    assert rendered[0].rating == 3
    assert rendered[0].rating_conclusion_override == "冻结评级结论。"
    assert rendered[0].overall_assessment == "冻结综合诊断结论。"
    assert rendered[0].actions == ["冻结建议。"]
    assert rendered[0].algorithm_audit_status == "available"
    assert rendered[0].rating_evidence == {"source": "frozen audit"}
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["formal_diagnosis"]["run_dir"] == str(tmp_path / "work/formal-run")
    assert len(manifest["formal_diagnosis"]["tree_sha256"]) == 64


def test_formal_curve_human_override_requires_current_review_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw").mkdir()
    run_root = tmp_path / "work/formal-run"
    run_root.mkdir(parents=True)
    project_manifest = run_root / "00_project_run_manifest.json"
    project_manifest.write_text("{}", encoding="utf-8")
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-09-05"
[paths]
raw = "raw"
work = "work"
output_docx = "report.docx"
formal_diagnosis_run = "work/formal-run"
manual_interpretations = "work/manual_curve_interpretations.json"
[report_layout]
only_valves = ["FV-1"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "work/analysis_summary.json").write_text(
        json.dumps([{"folder": "FV-1", "report_pages": []}]),
        encoding="utf-8",
    )
    (tmp_path / "work/curve_review_manifest.json").write_text(
        json.dumps({"images": [{"image_key": "stable-a", "test_key": "run_step"}]}),
        encoding="utf-8",
    )
    (tmp_path / "work/manual_curve_interpretations.json").write_text(
        json.dumps(
            {
                "interpretations": [
                    {
                        "image_key": "stable-a",
                        "test_key": "run_step",
                        "observation": "人工观察。",
                        "diagnosis": "人工判断。",
                        "rating": 4,
                        "action": "人工建议。",
                        "customer_text": "人工最终逐图判读。",
                        "confidence": "high",
                        "experience_rule_ids": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    frozen_curve = CurveInterpretation(
        image_key="stable-a",
        test_key="run_step",
        observation="冻结观察。",
        diagnosis="冻结判断。",
        rating=2,
        action="冻结建议。",
        customer_text="冻结逐图判读。",
        confidence="high",
        experience_rule_ids=(),
    )
    formal = FormalDiagnosis(
        curve_interpretations=(frozen_curve,),
        summary_overrides={
            "FV-1": {
                "rating": 2,
                "rating_conclusion": "冻结评级结论。",
                "main_issue": "冻结问题",
                "issues": [],
                "action": "冻结建议。",
                "actions": ["冻结建议。"],
                "overall_assessment": "冻结综合诊断结论。",
            }
        },
    )
    monkeypatch.setattr(pipeline, "load_formal_diagnosis", lambda *_args, **_kwargs: formal)

    with pytest.raises(RuntimeError, match="manual curve overlay.*no registered"):
        pipeline.run_project(tmp_path, skip_pdf=True)

    (tmp_path / "work/change_list.json").write_text(
        json.dumps(
            {
                "formal_diagnosis": {
                    "tree_sha256": pipeline._tree_sha256(run_root),
                    "project_manifest_sha256": pipeline._sha256(project_manifest),
                }
            }
        ),
        encoding="utf-8",
    )
    from offline_engine import generator
    configured: dict[str, object] = {}

    def fake_configure(*_args, **kwargs):
        configured.update(kwargs)
        generator.DIAGNOSTIC_SUMMARY_OVERRIDES = {
            tag: dict(item)
            for tag, item in kwargs["formal_summary_overrides"].items()
        }

    monkeypatch.setattr(generator, "configure", fake_configure)
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda **_kwargs: [generator.ValveReport(folder="FV-1")],
    )
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", lambda *_args: None)
    monkeypatch.setattr(pipeline, "validate_report", lambda *_args, **_kwargs: {"ok": True})

    result = pipeline.run_project(tmp_path, skip_pdf=True)

    assert result.status == "generated"
    assert configured["curve_interpretations"][0].customer_text == "人工最终逐图判读。"


def test_frozen_rating_filter_runs_after_formal_summary_and_data_keeps_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "raw/FV-2").mkdir(parents=True)
    (tmp_path / "raw/FV-5").mkdir()
    (tmp_path / "work/formal-run").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-09-05"
[paths]
raw = "raw"
work = "work"
output_docx = "report.docx"
formal_diagnosis_run = "work/formal-run"
[features]
include_data_insufficient_valves = false
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "work/curve_review_manifest.json").write_text(
        json.dumps({"images": []}),
        encoding="utf-8",
    )
    (tmp_path / "work/analysis_summary.json").write_text(
        json.dumps([{"folder": "FV-2"}, {"folder": "FV-5"}]),
        encoding="utf-8",
    )

    def summary(rating: int) -> dict[str, object]:
        return {
            "rating": rating,
            "rating_conclusion": f"冻结{rating}级结论。",
            "main_issue": "冻结问题",
            "issues": [],
            "action": "维持周期复测。",
            "actions": ["维持周期复测。"],
            "overall_assessment": f"冻结{rating}级综合结论。",
        }

    formal = FormalDiagnosis(
        curve_interpretations=(),
        summary_overrides={"FV-2": summary(2), "FV-5": summary(5)},
    )
    from offline_engine import generator
    add_unbound_human_summary = False

    def fake_configure(*_args, **kwargs):
        generator.DIAGNOSTIC_SUMMARY_OVERRIDES = {
            tag: dict(item)
            for tag, item in (kwargs.get("formal_summary_overrides") or {}).items()
        }
        if add_unbound_human_summary:
            generator.DIAGNOSTIC_SUMMARY_OVERRIDES["FV-5"] = summary(2)

    monkeypatch.setattr(pipeline, "load_formal_diagnosis", lambda *_args, **_kwargs: formal)
    monkeypatch.setattr(generator, "configure", fake_configure)
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda **_kwargs: [
            generator.ValveReport(folder="FV-2"),
            generator.ValveReport(folder="FV-5"),
        ],
    )
    summary_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        generator,
        "write_summary",
        lambda *_args, **kwargs: summary_calls.append(kwargs) or [],
    )
    rendered: list[list[str]] = []
    monkeypatch.setattr(
        pipeline,
        "_render_prepared_report",
        lambda _config, valves: rendered.append([valve.folder for valve in valves]),
    )
    monkeypatch.setattr(pipeline, "validate_report", lambda *_args, **_kwargs: {"ok": True})

    config = load_project(tmp_path)
    pipeline.run_project(tmp_path, config_override=config, skip_pdf=True)
    data_config = replace(
        config,
        presentation={**config.presentation, "data_only": True},
    )
    pipeline.run_project(tmp_path, config_override=data_config, skip_pdf=True)

    assert rendered == [["FV-2"], ["FV-2", "FV-5"]]
    assert summary_calls[0]["diagnosis_folders"] == {"FV-2", "FV-5"}
    assert summary_calls[1]["include_diagnosis"] is False

    del formal.summary_overrides["FV-5"]
    add_unbound_human_summary = True
    with pytest.raises(
        pipeline.FormalDiagnosisError,
        match="FV-5.*missing.*validated frozen diagnosis",
    ):
        pipeline.run_project(tmp_path, config_override=config, skip_pdf=True)


def test_formal_generation_requires_current_analysis_summary(
    tmp_path: Path,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work/formal-run").mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
formal_diagnosis_run = "work/formal-run"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "work/curve_review_manifest.json").write_text(
        json.dumps({"images": []}),
        encoding="utf-8",
    )

    with pytest.raises(
        pipeline.FormalDiagnosisError,
        match="prepare-formal-evidence",
    ):
        pipeline.run_project(tmp_path, skip_pdf=True)


def test_standard_generation_reuses_complete_curve_review(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [{"image_key": "stable-a", "test_key": "run_step"}]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "曲线响应连续。", "diagnosis": "未见明显异常。",
        "rating": 1, "action": "维持复测", "customer_text": "曲线响应连续，未见明显异常。", "confidence": "high",
        "experience_rule_ids": []
    }]), encoding="utf-8")
    from offline_engine import generator
    configured: dict[str, object] = {}
    validated: dict[str, object] = {}
    monkeypatch.setattr(generator, "configure", lambda *_args, **kwargs: configured.update(kwargs))
    monkeypatch.setattr(generator, "prepare_valve_data", lambda **_kwargs: [])
    monkeypatch.setattr(generator, "write_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_render_prepared_report", lambda *_args: None)
    monkeypatch.setattr(
        pipeline,
        "validate_report",
        lambda *_args, **kwargs: validated.update(kwargs) or {"ok": True},
    )

    with pytest.raises(pipeline.FormalDiagnosisError, match="binding is required"):
        run_project(tmp_path, skip_pdf=True)

    assert configured == {}
    assert validated == {}


def test_finalize_blocks_before_generator_when_spec_ocr_cache_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/spec.pdf").write_bytes(b"specification-placeholder")
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
specs = "specs"
work = "work"
output_docx = "Demo.docx"
[presentation]
data_only = true
""".strip(), encoding="utf-8")
    (tmp_path / "work/curve_review_manifest.json").write_text(json.dumps({
        "images": [{"image_key": "stable-a", "test_key": "run_step"}]
    }), encoding="utf-8")
    (tmp_path / "work/codex_curve_interpretations.json").write_text(json.dumps([{
        "image_key": "stable-a", "test_key": "run_step", "observation": "正常", "diagnosis": "正常",
        "rating": 1, "action": "维持复测", "customer_text": "未见异常", "confidence": "high",
        "experience_rule_ids": []
    }]), encoding="utf-8")
    from offline_engine import generator
    monkeypatch.setattr(
        generator,
        "prepare_valve_data",
        lambda: (_ for _ in ()).throw(AssertionError("valve preparation must not run without spec OCR cache")),
    )

    with pytest.raises(RuntimeError, match="extract-materials"):
        run_project(tmp_path, skip_pdf=True)


def test_prepare_curve_review_cli_rejects_incompatible_flags(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--prepare-curve-review", "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--prepare-curve-review cannot be combined with --dry-run" in result.stderr


def test_cli_exposes_staged_report_and_project_ocr_commands() -> None:
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    for flag in (
        "--prepare-ocr",
        "--generate-review",
        "--generate-data-only",
        "--ingest-review",
        "--generate-business",
    ):
        assert flag in result.stdout


def test_cli_ingest_review_does_not_require_material_or_parameter_approval(
    tmp_path: Path,
) -> None:
    project = tmp_path / "DemoProject"
    (project / "raw/FV-101").mkdir(parents=True)
    (project / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-09-04"
[paths]
raw = "raw"
work = "work/shared_engine"
output_docx = "legacy.docx"
""".strip(),
        encoding="utf-8",
    )
    source = project / "output/review/DemoProject-人工校验版-v1.docx"
    returned = project / "incoming/returned.docx"
    from test_review_workflow import _write_review

    _write_review(source, rating="", conclusion="", action="")
    _write_review(returned, rating="3级", conclusion="存在迟滞。", action="窗口期复测。")
    _register_review_source(project, source)
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project",
            str(project),
            "--ingest-review",
            str(returned),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "status=review_return_ingested" in result.stdout


@pytest.mark.parametrize("incompatible_flag", ["--dry-run", "--validate-only"])
def test_finalize_curve_review_cli_rejects_non_generation_flags(tmp_path: Path, incompatible_flag: str) -> None:
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"

    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--finalize-curve-review", incompatible_flag],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--finalize-curve-review cannot be combined" in result.stderr


@pytest.mark.parametrize("incompatible_flag", ["--skip-pdf", "--validate-only"])
def test_prepare_curve_review_cli_rejects_final_report_flags(tmp_path: Path, incompatible_flag: str) -> None:
    script = Path(__file__).resolve().parents[1] / "run_offline_report.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "--prepare-curve-review", incompatible_flag],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert f"--prepare-curve-review cannot be combined with {incompatible_flag}" in result.stderr


def test_prepare_curve_review_keeps_image_key_when_another_image_is_inserted(tmp_path: Path, monkeypatch) -> None:
    raw_valve_dir = tmp_path / "raw/FV-101"
    raw_valve_dir.mkdir(parents=True)
    (tmp_path / "project.toml").write_text(
        """
[project]
customer = "Demo"
report_title = "Report"
report_date = "2026-07-12"
[paths]
raw = "raw"
work = "work"
output_docx = "Demo.docx"
""".strip(),
        encoding="utf-8",
    )
    from offline_engine import generator

    original_image = {
        "path": str(tmp_path / "work/report_images/FV-101/original.png"),
        "source": "sequence.pdf",
        "page": "3",
        "bbox": "1,2,3,4",
        "test_key": "run_step",
    }
    inserted_image = {
        "path": str(tmp_path / "work/report_images/FV-101/inserted.png"),
        "source": "another-sequence.pdf",
        "page": "1",
        "bbox": "5,6,7,8",
        "test_key": "run_step",
    }
    images = [original_image]

    def render_review_image(folder: Path, *, include_observation: bool = True, trusted_device_id: str = ""):
        assert folder == raw_valve_dir
        assert include_observation is False
        return images

    monkeypatch.setattr(generator, "render_images_for_valve", render_review_image)
    first_manifest = json.loads(pipeline.prepare_curve_review(tmp_path).manifest_path.read_text(encoding="utf-8"))
    original_key = first_manifest["images"][0]["image_key"]

    images[:] = [inserted_image, original_image]
    second_manifest = json.loads(pipeline.prepare_curve_review(tmp_path).manifest_path.read_text(encoding="utf-8"))
    second_key_by_path = {image["image_path"]: image["image_key"] for image in second_manifest["images"]}

    assert second_key_by_path[str(Path(original_image["path"]).resolve())] == original_key


def test_curve_review_key_changes_when_rendered_image_content_changes(tmp_path: Path) -> None:
    image_path = tmp_path / "curve.png"
    image = {
        "path": str(image_path),
        "source": "sequence.pdf",
        "page": "3",
        "bbox": "1,2,3,4",
        "test_key": "run_step",
    }
    image_path.write_bytes(b"first-render")
    first_key = pipeline._curve_image_key("FV-101", image)

    image_path.write_bytes(b"changed-render")
    second_key = pipeline._curve_image_key("FV-101", image)

    assert second_key != first_key


def test_pipeline_and_generator_use_identical_curve_review_key(tmp_path: Path) -> None:
    from offline_engine import generator

    path = tmp_path / "curve.png"
    path.write_bytes(b"same-render")
    image = {"path": str(path), "source": "sequence.pdf", "page": "3", "test_key": "run_step"}

    assert pipeline._curve_image_key("FV-101", image) == generator.curve_review_image_key("FV-101", image)
