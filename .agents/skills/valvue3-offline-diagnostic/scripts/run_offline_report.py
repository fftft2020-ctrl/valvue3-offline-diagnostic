from __future__ import annotations

import argparse
from pathlib import Path

from export_wps_pdf import export_wps_pdf
from offline_engine.unit_reports import assemble_unit_reports
from offline_engine.pipeline import (
    approve_project_materials,
    approve_project_parameters,
    check_report_information,
    extract_project_materials,
    generate_business_report,
    generate_data_report,
    generate_review_report,
    ingest_review_report,
    prepare_curve_review,
    prepare_format_only_revision,
    prepare_formal_evidence,
    prepare_project_materials,
    prepare_project_ocr,
    require_project_materials_approval,
    require_project_parameters_approval,
    run_project,
    validate_wps_delivery,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare, generate, and validate a ValVue3 offline diagnostic project."
    )
    parser.add_argument("--project", required=True, type=Path, help="Project directory containing project.toml")
    parser.add_argument(
        "--report-period",
        help="Select a configured report period without changing project.toml active",
    )
    parser.add_argument(
        "--prepare-materials",
        action="store_true",
        help=(
            "Archive/inventory project files and write the frozen Stage 1 "
            "material report for user approval"
        ),
    )
    parser.add_argument(
        "--approve-materials",
        metavar="APPROVAL_TEXT",
        help=(
            "Record the user's explicit approval of the current frozen "
            "Stage 1 material archive report"
        ),
    )
    parser.add_argument(
        "--unchanged-reuse-policy",
        choices=("reuse_unchanged", "reanalyze_all"),
        help=(
            "Required with --approve-materials when Stage 1 finds valves "
            "unchanged from the configured frozen baseline"
        ),
    )
    parser.add_argument(
        "--approve-parameters",
        metavar="APPROVAL_TEXT",
        help=(
            "Record the user's explicit approval of the current frozen "
            "Stage 2 parameter extraction report"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration only; do not assess materials or load diagnosis",
    )
    parser.add_argument("--skip-pdf", action="store_true", help="Generate and validate DOCX without the internal LibreOffice check PDF")
    parser.add_argument("--validate-only", action="store_true", help="Validate the existing DOCX without regenerating")
    parser.add_argument(
        "--finalize-curve-review",
        action="store_true",
        help="Read validated frozen diagnoses and bound human overrides, then generate the customer report",
    )
    parser.add_argument(
        "--prepare-curve-review",
        action="store_true",
        help="Render curve crops and write a review manifest without generating DOCX/PDF",
    )
    parser.add_argument(
        "--prepare-formal-evidence",
        action="store_true",
        help="Build the filtered analysis snapshot for a formal single-valve diagnosis run",
    )
    parser.add_argument("--evidence-work-dir", type=Path,
                        help="Separate project/work directory for evidence preparation only")
    parser.add_argument(
        "--prepare-spec-ocr",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--prepare-ocr",
        action="store_true",
        help="Prepare or reuse project-local PDF text and OCR cache",
    )
    parser.add_argument(
        "--extract-materials",
        action="store_true",
        help="Extract fresh per-valve parameters and evidence from current project materials",
    )
    parser.add_argument(
        "--retry-failed-ocr",
        action="store_true",
        help="Retry specification pages whose prior local OCR attempt failed",
    )
    parser.add_argument(
        "--generate-review",
        action="store_true",
        help="Generate the next human-review DOCX and internal LibreOffice check PDF",
    )
    parser.add_argument(
        "--prepare-format-only",
        type=Path,
        help=(
            "Copy a confirmed project DOCX into the next version for "
            "formatting-only edits without regenerating report content"
        ),
    )
    parser.add_argument(
        "--generate-data-only",
        action="store_true",
        help="Generate the next factual-data DOCX and internal LibreOffice check PDF",
    )
    parser.add_argument(
        "--ingest-review",
        type=Path,
        help="Archive and compare a human-edited review DOCX",
    )
    parser.add_argument("--review-base", type=Path, help="Explicit registered DOCX sent for human review")
    parser.add_argument("--review-return", type=Path, help="Registered change_list.json for business generation")
    parser.add_argument(
        "--generate-business",
        action="store_true",
        help="Generate the next business DOCX and internal LibreOffice check PDF from the latest parsed human return",
    )
    parser.add_argument(
        "--assemble-units",
        type=Path,
        help="Assemble one DOCX per unit from selected human-reviewed reports",
    )
    parser.add_argument(
        "--check-report-information",
        type=Path,
        help=(
            "Run the single compact acceptance check for required customer "
            "information in an existing DOCX"
        ),
    )
    parser.add_argument(
        "--check-report-pdf",
        type=Path,
        help="Optional PDF corresponding to --check-report-information",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow explicitly accepted empty human fields in a business report",
    )
    parser.add_argument(
        "--export-wps-docx",
        type=Path,
        help=(
            "Export a confirmed DOCX to a same-directory, same-stem PDF "
            "through the shared WPS RPC exporter"
        ),
    )
    parser.add_argument(
        "--validate-wps-pdf",
        type=Path,
        help="Validate a WPS-exported PDF for delivery",
    )
    parser.add_argument(
        "--delivery-docx",
        type=Path,
        help="DOCX that corresponds to --validate-wps-pdf",
    )
    parser.add_argument(
        "--format-only-source",
        type=Path,
        help=(
            "Confirmed parent DOCX whose content must remain identical "
            "during WPS delivery validation"
        ),
    )
    parser.add_argument(
        "--approve-wps-layout",
        action="store_true",
        help="Approve the generated WPS contact sheet after actual visual review",
    )
    parser.add_argument(
        "--wps-review-note",
        default="",
        help="Required visual review note when --approve-wps-layout is used",
    )
    args = parser.parse_args()
    if args.prepare_spec_ocr:
        parser.error("--prepare-spec-ocr is retired; use --extract-materials (or --prepare-ocr for troubleshooting)")
    if (
        args.unchanged_reuse_policy is not None
        and args.approve_materials is None
    ):
        parser.error(
            "--unchanged-reuse-policy requires --approve-materials"
        )
    if args.retry_failed_ocr and not (args.prepare_ocr or args.extract_materials):
        parser.error("--retry-failed-ocr requires --prepare-ocr or --extract-materials")
    if args.dry_run and (args.skip_pdf or args.validate_only):
        parser.error("--dry-run cannot be combined with --skip-pdf or --validate-only")
    if args.skip_pdf and args.validate_only:
        parser.error("--skip-pdf and --validate-only are mutually exclusive")
    if args.finalize_curve_review and (args.dry_run or args.validate_only):
        parser.error("--finalize-curve-review cannot be combined with --dry-run or --validate-only")
    new_operations = [
        args.prepare_materials,
        args.approve_materials is not None,
        args.approve_parameters is not None,
        args.prepare_ocr,
        args.extract_materials,
        args.prepare_format_only is not None,
        args.generate_review,
        args.generate_data_only,
        args.ingest_review is not None,
        args.generate_business,
        args.assemble_units is not None,
        args.check_report_information is not None,
        args.export_wps_docx is not None,
        args.validate_wps_pdf is not None,
    ]
    old_operations = [
        args.prepare_curve_review,
        args.prepare_formal_evidence,
        args.finalize_curve_review,
    ]
    if sum(bool(value) for value in new_operations + old_operations) > 1:
        parser.error(
            "select only one project preparation, report, or delivery operation"
        )
    if args.evidence_work_dir is not None and not (args.prepare_curve_review or args.prepare_formal_evidence):
        parser.error("--evidence-work-dir only applies to evidence preparation")
    if any(new_operations) and (args.dry_run or args.validate_only):
        parser.error(
            "the selected operation cannot be combined with --dry-run or --validate-only"
        )
    if args.skip_pdf and (
        args.prepare_materials
        or args.approve_materials is not None
        or args.approve_parameters is not None
        or args.prepare_ocr
        or args.extract_materials
        or args.ingest_review is not None
        or args.assemble_units is not None
        or args.validate_wps_pdf is not None
        or args.prepare_format_only is not None
        or args.check_report_information is not None
        or args.export_wps_docx is not None
    ):
        parser.error("--skip-pdf only applies to report generation")
    if args.review_base is not None and args.ingest_review is None:
        parser.error("--review-base requires --ingest-review")
    if args.review_return is not None and not args.generate_business:
        parser.error("--review-return requires --generate-business")
    if args.allow_empty and not args.generate_business:
        parser.error("--allow-empty requires --generate-business")
    if args.validate_wps_pdf is not None and args.delivery_docx is None:
        parser.error("--validate-wps-pdf requires --delivery-docx")
    if args.delivery_docx is not None and args.validate_wps_pdf is None:
        parser.error("--delivery-docx requires --validate-wps-pdf")
    if (
        args.format_only_source is not None
        and args.validate_wps_pdf is None
    ):
        parser.error("--format-only-source requires --validate-wps-pdf")
    if args.approve_wps_layout and args.validate_wps_pdf is None:
        parser.error("--approve-wps-layout requires --validate-wps-pdf")
    if args.wps_review_note and not args.approve_wps_layout:
        parser.error("--wps-review-note requires --approve-wps-layout")
    if args.check_report_pdf is not None and args.check_report_information is None:
        parser.error("--check-report-pdf requires --check-report-information")
    selected_old_operation = (
        "prepare-curve-review"
        if args.prepare_curve_review
        else "prepare-formal-evidence"
        if args.prepare_formal_evidence
        else ""
    )
    if selected_old_operation:
        for name in (
            "dry_run",
            "skip_pdf",
            "validate_only",
            "finalize_curve_review",
            "prepare_curve_review",
            "prepare_formal_evidence",
        ):
            if (
                name != selected_old_operation.replace("-", "_")
                and getattr(args, name)
            ):
                parser.error(
                    f"--{selected_old_operation} cannot be combined with "
                    f"--{name.replace('_', '-')}"
                )
    approval_exempt = (
        args.prepare_materials
        or args.approve_materials is not None
        or args.dry_run
        or args.validate_only
        or args.prepare_format_only is not None
        or args.ingest_review is not None
        or args.generate_business
        or args.assemble_units is not None
        or args.check_report_information is not None
        or args.export_wps_docx is not None
        or args.validate_wps_pdf is not None
    )
    if not approval_exempt:
        try:
            require_project_materials_approval(
                args.project,
                report_period=args.report_period,
            )
        except RuntimeError as exc:
            parser.error(str(exc))
    # Stage 2 is a separate hard gate.  Extraction and its supporting cache
    # operations are allowed after Stage 1 so that the user can review the
    # parameter snapshot; all curve/diagnostic/report operations require the
    # explicit Stage 2 approval.  Read-only checks and format/WPS operations
    # remain intentionally exempt.
    stage2_exempt = (
        args.prepare_materials
        or args.approve_materials is not None
        or args.approve_parameters is not None
        or args.prepare_ocr
        or args.extract_materials
        or args.dry_run
        or args.validate_only
        or args.prepare_format_only is not None
        or args.ingest_review is not None
        or args.generate_business
        or args.assemble_units is not None
        or args.check_report_information is not None
        or args.export_wps_docx is not None
        or args.validate_wps_pdf is not None
    )
    if not stage2_exempt:
        try:
            require_project_parameters_approval(
                args.project,
                report_period=args.report_period,
            )
        except RuntimeError as exc:
            parser.error(str(exc))
    if args.export_wps_docx is not None:
        source = args.export_wps_docx.resolve()
        project_output = (args.project.resolve() / "output").resolve()
        if not source.is_relative_to(project_output):
            parser.error("--export-wps-docx must be inside the project output directory")
        exported = export_wps_pdf(
            source,
            source.with_suffix(".pdf"),
        )
        for key, value in exported.items():
            print(f"{key.upper()}={value}")
        return
    if args.check_report_information is not None:
        result = check_report_information(
            args.project,
            docx=args.check_report_information,
            pdf=args.check_report_pdf,
            report_period=args.report_period,
        )
    elif args.validate_wps_pdf is not None:
        result = validate_wps_delivery(
            args.project,
            docx=args.delivery_docx,
            wps_pdf=args.validate_wps_pdf,
            approve_visual=args.approve_wps_layout,
            visual_review_note=args.wps_review_note,
            report_period=args.report_period,
            format_only_source=args.format_only_source,
        )
    elif args.prepare_format_only is not None:
        result = prepare_format_only_revision(
            args.project,
            source_docx=args.prepare_format_only,
            report_period=args.report_period,
        )
    elif args.prepare_materials:
        result = prepare_project_materials(
            args.project,
            report_period=args.report_period,
        )
    elif args.approve_materials is not None:
        result = approve_project_materials(
            args.project,
            approval_text=args.approve_materials,
            unchanged_reuse_policy=args.unchanged_reuse_policy,
            report_period=args.report_period,
        )
    elif args.approve_parameters is not None:
        result = approve_project_parameters(
            args.project,
            approval_text=args.approve_parameters,
            report_period=args.report_period,
        )
    elif args.prepare_ocr:
        result = prepare_project_ocr(
            args.project,
            report_period=args.report_period,
            retry_failed_ocr=args.retry_failed_ocr,
        )
    elif args.extract_materials:
        result = extract_project_materials(
            args.project,
            report_period=args.report_period,
            retry_failed_ocr=args.retry_failed_ocr,
        )
    elif args.generate_review:
        result = generate_review_report(
            args.project,
            skip_pdf=args.skip_pdf,
            report_period=args.report_period,
        )
    elif args.generate_data_only:
        result = generate_data_report(
            args.project,
            skip_pdf=args.skip_pdf,
            report_period=args.report_period,
        )
    elif args.ingest_review is not None:
        imported = ingest_review_report(args.project, args.ingest_review, review_base=args.review_base, report_period=args.report_period)
        print("status=review_return_ingested")
        print(f"manifest={imported.change_json}")
        return
    elif args.generate_business:
        result = generate_business_report(
            args.project,
            skip_pdf=args.skip_pdf,
            allow_empty=args.allow_empty,
            report_period=args.report_period,
            review_return=args.review_return,
        )
    elif args.assemble_units is not None:
        manifest = assemble_unit_reports(args.project, args.assemble_units)
        print("status=unit_reports_assembled")
        print(f"manifest={manifest}")
        return
    elif args.prepare_curve_review:
        result = prepare_curve_review(args.project, report_period=args.report_period,
                                      evidence_work_dir=args.evidence_work_dir)
    elif args.prepare_formal_evidence:
        result = prepare_formal_evidence(args.project, report_period=args.report_period,
                                         evidence_work_dir=args.evidence_work_dir)
    else:
        result = run_project(
            args.project,
            dry_run=args.dry_run,
            skip_pdf=args.skip_pdf,
            validate_only=args.validate_only,
            finalize_curve_review=args.finalize_curve_review,
            report_period=args.report_period,
        )
    print(f"status={result.status}")
    print(f"manifest={result.manifest_path}")
    if result.report_path is not None:
        print(f"report={result.report_path}")
    if result.web_report_path is not None:
        print(f"web_report={result.web_report_path}")
    if args.extract_materials:
        stage_gate_dir = result.config.project_dir / "work" / "stage-gates"
        print(
            "parameter_report="
            f"{stage_gate_dir / 'parameter-extraction-report.md'}"
        )
        print(
            "parameter_manifest="
            f"{stage_gate_dir / 'parameter-extraction-report.json'}"
        )
        print(
            "parameter_web_report="
            f"{stage_gate_dir / 'parameter-extraction-report.html'}"
        )
    if result.status == "format_revision_prepared":
        print(f"docx={result.config.output_docx}")
    if result.status in {"information_complete", "information_missing"}:
        import json

        acceptance = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        counts = acceptance.get("counts", {})
        print(
            "information="
            + " ".join(
                f"{name}:{value.get('confirmed', 0)}/{value.get('expected', 0)}"
                for name, value in counts.items()
            )
        )
        for issue in acceptance.get("summary_issues", acceptance.get("issues", [])):
            print(f"issue={issue}")
        if result.status == "information_missing":
            raise SystemExit(1)
    if result.missing_image_keys:
        print(f"missing_image_keys={','.join(result.missing_image_keys)}")


if __name__ == "__main__":
    main()
