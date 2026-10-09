from pathlib import Path

import pytest

from offline_engine.config import ConfigError, load_project


def write_project_toml(
    project_dir: Path,
    *,
    customer: str = "Demo",
    raw: str = "raw",
    output_docx: str = "Demo.docx",
    diagnostic_summary_overrides: str | None = None,
    formal_diagnosis_run: str | None = None,
    valve_context: str | None = None,
    presentation_profile: str | None = None,
) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = [
                "[project]",
                f'customer = "{customer}"',
                'report_title = "ValVue3 Offline Diagnostic Report"',
                'report_date = "2026-07-11"',
                "",
                "[paths]",
                f'raw = "{raw}"',
                'specs = "specs"',
                'inbound = "inbound"',
                'work = "work"',
                'output = "output"',
                f'output_docx = "{output_docx}"',
                'output_pdf = "Demo.pdf"',
            ]
    if diagnostic_summary_overrides is not None:
        lines.append(f'diagnostic_summary_overrides = "{diagnostic_summary_overrides}"')
    if formal_diagnosis_run is not None:
        lines.append(f'formal_diagnosis_run = "{formal_diagnosis_run}"')
    if valve_context is not None:
        lines.append(f'valve_context = "{valve_context}"')
    if presentation_profile is not None:
        lines.extend([
            "",
            "[presentation]",
            f'profile = "{presentation_profile}"',
            'brand_primary = "004B46"',
        ])
    (project_dir / "project.toml").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def test_load_project_resolves_paths_inside_project(tmp_path: Path) -> None:
    write_project_toml(tmp_path)

    config = load_project(tmp_path)

    assert config.raw_dir == tmp_path / "raw"
    assert config.output_docx == tmp_path / "output" / "Demo.docx"
    assert config.customer == "Demo"
    assert config.diagnostic_summary_overrides_file is None


def test_load_project_preserves_report_layout_configuration(tmp_path: Path) -> None:
    write_project_toml(tmp_path)
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_layout]\n'
            'append_valves = ["FV-NEW-2", "FV-NEW-1"]\n'
        )

    config = load_project(tmp_path)

    assert config.report_layout == {
        "append_valves": ["FV-NEW-2", "FV-NEW-1"],
    }


def test_load_project_resolves_active_report_period_label(tmp_path: Path) -> None:
    write_project_toml(tmp_path)
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "completed_batch"\n'
            'completed_batch_label = "2026年7月18日至7月21日"\n'
            'rolling_batch_label = "2026年7月22日至今"\n'
        )

    config = load_project(tmp_path)

    assert config.report_period_label == "2026年7月18日至7月21日"


def test_report_period_can_override_report_date(tmp_path: Path) -> None:
    write_project_toml(tmp_path)
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "new_batch"\n'
            'new_batch_label = "2026年7月25日至8月3日"\n'
            '\n[report_period_overrides.new_batch]\n'
            'report_date = "2026-08-03"\n'
        )

    config = load_project(tmp_path)

    assert config.report_date == "2026-08-03"
    assert "report_date" not in config.report_layout


def test_load_project_explicit_period_selects_valves_and_formal_run(tmp_path: Path) -> None:
    write_project_toml(
        tmp_path,
        formal_diagnosis_run="work/formal/default",
    )
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "rolling_batch"\n'
            'completed_batch_label = "2026年7月18日至7月21日"\n'
            'rolling_batch_label = "2026年7月22日至今"\n'
            '\n[report_layout]\n'
            'only_valves = ["FV-ROLLING"]\n'
            '\n[report_period_overrides.completed_batch]\n'
            'work = "work/completed_batch"\n'
            'manual_interpretations = "work/completed_batch/manual_curve_interpretations.json"\n'
            'formal_diagnosis_run = "work/single_valve_runs/completed_batch"\n'
            'only_valves = ["FV-OLD"]\n'
        )

    config = load_project(tmp_path, report_period="completed_batch")

    assert config.report_period_key == "completed_batch"
    assert config.report_period_label == "2026年7月18日至7月21日"
    assert config.report_layout["only_valves"] == ["FV-OLD"]
    assert "work" not in config.report_layout
    assert "manual_interpretations" not in config.report_layout
    assert config.work_dir == tmp_path / "work/completed_batch"
    assert config.manual_interpretations_file == (
        tmp_path / "work/completed_batch/manual_curve_interpretations.json"
    )
    assert config.formal_diagnosis_run_dir == (
        tmp_path / "work/single_valve_runs/completed_batch"
    )


def test_load_project_rejects_unknown_explicit_period(tmp_path: Path) -> None:
    write_project_toml(tmp_path)
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "rolling_batch"\n'
            'rolling_batch_label = "2026年7月22日至今"\n'
        )

    with pytest.raises(ConfigError, match="report_periods.completed_batch_label"):
        load_project(tmp_path, report_period="completed_batch")


def test_load_project_resolves_optional_diagnostic_summary_overrides(tmp_path: Path) -> None:
    write_project_toml(
        tmp_path,
        diagnostic_summary_overrides="experience/diagnostic_summary_overrides.json",
    )

    config = load_project(tmp_path)

    assert config.diagnostic_summary_overrides_file == (
        tmp_path / "experience/diagnostic_summary_overrides.json"
    )


def test_report_period_can_disable_project_diagnostic_summary_overrides(tmp_path: Path) -> None:
    write_project_toml(
        tmp_path,
        diagnostic_summary_overrides="experience/diagnostic_summary_overrides.json",
    )
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "new_batch"\n'
            'new_batch_label = "2026年7月25日至8月3日"\n'
            '\n[report_period_overrides.new_batch]\n'
            'diagnostic_summary_overrides = ""\n'
        )

    config = load_project(tmp_path)

    assert config.diagnostic_summary_overrides_file is None
    assert "diagnostic_summary_overrides" not in config.report_layout


def test_report_period_can_override_features_without_leaking_into_layout(
    tmp_path: Path,
) -> None:
    write_project_toml(tmp_path)
    with (tmp_path / "project.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n\n[report_periods]\n'
            'active = "no_specs"\n'
            'no_specs_label = "无规格书子集"\n'
            '\n[report_period_overrides.no_specs.features]\n'
            'include_specifications = false\n'
        )

    config = load_project(tmp_path)

    assert config.features["include_specifications"] is False
    assert "features" not in config.report_layout


def test_load_project_resolves_optional_formal_diagnosis_run(tmp_path: Path) -> None:
    write_project_toml(
        tmp_path,
        formal_diagnosis_run="work/formal_diagnosis/runs/batch-1",
    )

    config = load_project(tmp_path)

    assert config.formal_diagnosis_run_dir == (
        tmp_path / "work/formal_diagnosis/runs/batch-1"
    )


def test_load_project_rejects_formal_diagnosis_path_escape(tmp_path: Path) -> None:
    write_project_toml(tmp_path, formal_diagnosis_run="../runs/batch-1")

    with pytest.raises(ConfigError, match="outside project"):
        load_project(tmp_path)


def test_load_project_resolves_optional_valve_context(tmp_path: Path) -> None:
    write_project_toml(
        tmp_path,
        valve_context="experience/valve_context.json",
    )

    config = load_project(tmp_path)

    assert config.valve_context_file == tmp_path / "experience/valve_context.json"


def test_load_project_rejects_valve_context_path_escape(tmp_path: Path) -> None:
    write_project_toml(tmp_path, valve_context="../valve_context.json")

    with pytest.raises(ConfigError, match="outside project"):
        load_project(tmp_path)


def test_load_project_rejects_diagnostic_summary_path_escape(tmp_path: Path) -> None:
    write_project_toml(tmp_path, diagnostic_summary_overrides="../summary.json")

    with pytest.raises(ConfigError, match="outside project"):
        load_project(tmp_path)


def test_load_project_rejects_path_escape(tmp_path: Path) -> None:
    write_project_toml(tmp_path, raw="../other")

    with pytest.raises(ConfigError, match="outside project"):
        load_project(tmp_path)


def test_load_project_rejects_missing_customer(tmp_path: Path) -> None:
    write_project_toml(tmp_path, customer="")

    with pytest.raises(ConfigError, match="project.customer"):
        load_project(tmp_path)


def test_load_project_reads_optional_presentation_profile(tmp_path: Path) -> None:
    write_project_toml(tmp_path, presentation_profile="business_dashboard")

    config = load_project(tmp_path)

    assert config.presentation == {
        "include_field_context_overview": False,
        "include_overview_judgement": False,
        "include_overview_priority": False,
        "include_retest_recommendations": False,
        "profile": "business_dashboard",
        "brand_primary": "004B46",
    }


def test_load_project_defaults_to_disabled_optional_overview_sections(
    tmp_path: Path,
) -> None:
    write_project_toml(tmp_path)

    config = load_project(tmp_path)

    assert config.presentation == {
        "include_field_context_overview": False,
        "include_overview_judgement": False,
        "include_overview_priority": False,
        "include_retest_recommendations": False,
    }
