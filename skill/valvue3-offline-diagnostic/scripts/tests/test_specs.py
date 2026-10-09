import json
import re
from pathlib import Path

import fitz
import pytest

from init_offline_project import initialize_project
from offline_engine import generator
from offline_engine.config import ProjectConfig, load_project


EMPTY_EXPERIENCE = {"rules": [], "feedback_files": []}


def _create_project(
    root: Path,
    *,
    name: str = "PROJECT-001",
    include_accessory_pages: bool = True,
) -> tuple[Path, ProjectConfig]:
    project, _ = initialize_project(
        root / name,
        customer="Anonymous Customer",
        report_title="ValVue3 Offline Diagnostic Report",
        report_date="2026-08-26",
    )
    config_path = project / "project.toml"
    config_path.write_text(
        re.sub(
            r"include_accessory_pages\s*=\s*(?:true|false)",
            f"include_accessory_pages = {str(include_accessory_pages).lower()}",
            config_path.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    return project, load_project(project)


def _write_blank_pdf(path: Path, page_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    document.save(path)
    document.close()


def _configure(config, *, page_cache=None) -> None:
    generator.configure(
        config,
        spec_page_text_cache=page_cache,
    )


def test_extract_spec_tags_expands_multiple_letter_suffixes() -> None:
    assert generator.extract_spec_tags("Tag 1000-FV-1001A/B/C/D") == {
        "1000-FV-1001A",
        "1000-FV-1001B",
        "1000-FV-1001C",
        "1000-FV-1001D",
    }


def test_extract_spec_tags_expands_alternate_tag_number() -> None:
    assert generator.extract_spec_tags("Tag 1000-PV-2001C/2002D") == {
        "1000-PV-2001C",
        "1000-PV-2002D",
    }


def test_specification_sheet_accepts_confirmed_ocr_typo() -> None:
    assert generator.is_specification_sheet("Specffication Sheet")
    assert generator.is_specification_sheet("CONTROL VALVE SPECIFICATIONS")
    assert generator.is_specification_sheet("CONTROLVALVE SPECIFICATIONS")


@pytest.mark.parametrize("page_tag,expected", [("1000-FV-1001", True), ("1000-FV-1002", False), ("", False)])
def test_quality_plan_requires_page_identity_before_automatic_binding(tmp_path, page_tag, expected):
    _, config = _create_project(tmp_path)
    pdf = config.specs_dir / "1000-FV-1001/quality-plan.pdf"
    _write_blank_pdf(pdf, 1)
    text = (f"ASSEMBLY QUALITY PLAN\nTag: {page_tag}\nType: 88-12345\n"
            'Size: DN 150 (6")\nConnections: FLANGED CLASS 600\nSpring range: 15-25 psi')
    _configure(config, page_cache={(str(pdf.resolve()), 1): text})
    specs = generator.load_spec_pages()
    key = generator.normalize_spec_tag("1000-FV-1001")
    assert (key in specs) is expected
    if expected:
        assert specs[key].page == 1
        assert specs[key].fields["model_no"] == "88-12345"
        assert specs[key].fields["valve_size"] == "6 in"
        assert specs[key].fields["pressure_class"] == "CLASS 600"


def test_quality_plan_does_not_replace_an_existing_specification(tmp_path):
    _, config = _create_project(tmp_path)
    sheet = config.specs_dir / "1000-FV-1001/a-specification.pdf"
    quality = sheet.with_name("z-quality.pdf")
    for path in (sheet, quality):
        _write_blank_pdf(path, 1)
    _configure(config, page_cache={
        (str(sheet.resolve()), 1): "Specification Sheet\nTag 1000-FV-1001\nModel No. 88-11111",
        (str(quality.resolve()), 1): "ASSEMBLY QUALITY PLAN\nTag: 1000-FV-1001\nType: 88-22222"})
    spec = generator.load_spec_pages()[generator.normalize_spec_tag("1000-FV-1001")]
    assert spec.source.endswith("a-specification.pdf")


def test_quality_plan_cover_does_not_hide_later_specification_page(tmp_path):
    _, config = _create_project(tmp_path)
    pdf = config.specs_dir / "1000-FV-1001/combined.pdf"
    _write_blank_pdf(pdf, 2)
    _configure(config, page_cache={
        (str(pdf.resolve()), 1): "ASSEMBLY QUALITY PLAN\nTag: 1000-FV-1001\nType: 88-22222",
        (str(pdf.resolve()), 2): "Masoneilan Valves 88-11111 Specification Sheet\nTag 1000-FV-1001"})
    spec = generator.load_spec_pages()[generator.normalize_spec_tag("1000-FV-1001")]
    assert spec.page == 2
    assert spec.fields["model_no"] == "88-11111"


def test_quality_plan_keeps_reducing_dn_size_when_inch_marks_are_missing():
    fields = generator.parse_spec_fields("ASSEMBLY QUALITY PLAN\nSize D.N.: DN80×50 (3*2)")
    assert fields["valve_size"] == "DN80×50"


def test_page_roles_distinguish_air_piping_from_ordinary_accessory() -> None:
    assert generator.classify_spec_page_roles(
        "Specification / Spec Data Sheet Air Piping Diagram"
    ) == ("specification", "air_piping")
    assert generator.classify_spec_page_roles("Air Piping Diagram") == ("air_piping",)
    assert generator.classify_spec_page_roles("Accessory Sheet POSITIONER SVI2AP") == (
        "accessory",
    )


def test_merge_spec_pages_uses_source_hash_and_physical_page_identity() -> None:
    first = generator.SpecPage(
        valve="VALVE001", tag="VALVE-001", source="spec.pdf", page=2,
        source_sha256="abc", roles=("specification",), match_basis=("page_label",),
    )
    same_physical_page = generator.SpecPage(
        valve="VALVE001", tag="VALVE-001", source="moved/spec.pdf", page=2,
        source_sha256="abc", roles=("air_piping",), match_basis=("visual_review",),
    )
    visually_similar_other_page = generator.SpecPage(
        valve="VALVE001", tag="VALVE-001", source="spec.pdf", page=3,
        source_sha256="abc", roles=("air_piping",),
    )

    merged = generator.merge_spec_page_references(
        [first, same_physical_page, visually_similar_other_page]
    )

    assert len(merged) == 2
    assert merged[0].physical_identity == "abc:2"
    assert merged[0].roles == ("specification", "air_piping")
    assert merged[0].match_basis == ("page_label", "visual_review")
    assert merged[1].physical_identity == "abc:3"


def test_synthetic_multi_page_pdf_loads_all_specification_pages(
    tmp_path: Path,
) -> None:
    _, config = _create_project(tmp_path)
    pdf = config.specs_dir / "anonymous-specifications.pdf"
    _write_blank_pdf(pdf, 3)
    tags = ["1000-FV-1001", "1000-FV-1002", "1000-FV-1003"]
    page_cache = {
        (str(pdf.resolve()), page): (
            f"Specification / Spec Data Sheet\nTag {tag}\nModel No. MODEL-{page}"
        )
        for page, tag in enumerate(tags, start=1)
    }
    _configure(config, page_cache=page_cache)

    specs = generator.load_spec_pages()

    assert {spec.tag for spec in specs.values()} == set(tags)


def test_explicit_spec_mapping_reads_only_mapped_pages(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project, config = _create_project(tmp_path)
    pdf = config.specs_dir / "shared-specifications.pdf"
    _write_blank_pdf(pdf, 5)
    mapping = {
        "1000-FV-1001": {"source": pdf.name, "page": 2},
        "1000-FV-1002": {"source": pdf.name, "page": 4},
        "1000-FV-1003": {"source": pdf.name, "page": 5},
    }
    (project / "spec_page_mapping.json").write_text(
        json.dumps(mapping),
        encoding="utf-8",
    )
    _configure(config)
    calls: list[int] = []

    def fake_page_text(_pdf, _page, page_no):
        calls.append(page_no)
        return f"Specification / Spec Data Sheet Model No. MODEL-{page_no}"

    monkeypatch.setattr(generator, "page_text_with_ocr", fake_page_text)

    specs = generator.load_spec_pages()
    by_tag = {spec.tag: spec for spec in specs.values()}

    assert set(by_tag) == set(mapping)
    assert [by_tag[tag].page for tag in mapping] == [2, 4, 5]
    assert calls == [2, 4, 5]


def test_accessory_page_feature_is_project_configured(tmp_path: Path) -> None:
    _, enabled = _create_project(tmp_path, name="PROJECT-ENABLED")
    _, disabled = _create_project(
        tmp_path,
        name="PROJECT-DISABLED",
        include_accessory_pages=False,
    )

    _configure(enabled)
    assert generator.INCLUDE_ACCESSORY_PAGES is True

    _configure(disabled)
    assert generator.INCLUDE_ACCESSORY_PAGES is False


def test_project_spec_field_override_has_final_precedence(
    tmp_path: Path,
) -> None:
    project, config = _create_project(tmp_path)
    (project / "spec_field_overrides.json").write_text(
        json.dumps({"1000-FV-1001": {"model_no": "MODEL-OVERRIDE"}}),
        encoding="utf-8",
    )
    _configure(config)

    fields = generator.spec_fields_from_text(
        "CONTROL VALVE SPECIFICATIONS\nModel No. [MODEL-PARSED",
        "1000-FV-1001",
    )

    assert fields["model_no"] == "MODEL-OVERRIDE"


def test_nested_spec_pdf_uses_parent_directory_as_valve_tag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    specs_dir = tmp_path / "specs"
    pdf = specs_dir / "1000-FV-1001/spec.pdf"
    _write_blank_pdf(pdf, 1)
    monkeypatch.setattr(generator, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(
        generator,
        "page_text_with_ocr",
        lambda _pdf, _page, _page_no: (
            "Masoneilan Valves MODEL-1001 Specification Sheet Tag 1000-FV-1001"
        ),
    )

    specs = generator.load_spec_pages()

    spec = specs[generator.normalize_spec_tag("1000-FV-1001")]
    assert spec.tag == "1000-FV-1001"
    assert spec.source == "1000-FV-1001/spec.pdf"
    assert spec.page == 1


def test_nested_spec_pdf_rejects_parent_tag_when_page_has_different_unit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    specs_dir = tmp_path / "specs"
    pdf = specs_dir / "9100-HV-9001/spec.pdf"
    _write_blank_pdf(pdf, 1)
    monkeypatch.setattr(generator, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(
        generator,
        "page_text_with_ocr",
        lambda _pdf, _page, _page_no: "Specification Sheet\nTag\n9200-HV-9001",
    )

    specs = generator.load_spec_pages()

    assert generator.normalize_spec_tag("9100-HV-9001") not in specs


def test_accessory_fields_extract_positioner_model_from_cached_page_text() -> None:
    text = (
        "Accessory Sheet\n1000-FV-1001/1002\n"
        "Model\nPositioner\nSVI 2 AP - 31123111"
    )

    assert generator.parse_accessory_fields(text) == {
        "positioner_model": "SVI 2 AP - 31123111"
    }


def test_accessory_fields_extract_positioner_model_from_parts_table() -> None:
    text = (
        "Specification Sheet Tag 1000-FV-1001 Accessory Sheet "
        "PART NAME POSITIONER SVI2-22113121 AIR SET 78-40"
    )

    assert generator.parse_accessory_fields(text) == {
        "positioner_model": "SVI2-22113121"
    }


def test_configured_spec_source_requires_relative_path_when_basename_is_duplicated(
    tmp_path: Path,
) -> None:
    specs_dir = tmp_path / "specs"
    first = specs_dir / "A/spec.pdf"
    second = specs_dir / "B/spec.pdf"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.touch()
    second.touch()
    pdfs = [first, second]

    with pytest.raises(ValueError, match="ambiguous specification source"):
        generator.configured_spec_source_matches(
            "spec.pdf", first, pdfs, specs_dir
        )

    assert generator.configured_spec_source_matches(
        "A/spec.pdf", first, pdfs, specs_dir
    )
    assert not generator.configured_spec_source_matches(
        "A/spec.pdf", second, pdfs, specs_dir
    )
