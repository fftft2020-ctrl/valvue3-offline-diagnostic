#!/usr/bin/env python3
"""Export a confirmed DOCX through WPS without driving its GUI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz


def _require_ok(hr: int, action: str) -> None:
    if hr != 0:
        raise RuntimeError(f"{action} failed: HRESULT={hr}")


def _validate_paths(source: Path, target: Path) -> tuple[Path, Path]:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise ValueError(f"invalid DOCX: {source}")
    if target.suffix.lower() != ".pdf":
        raise ValueError(f"output must be a PDF: {target}")
    if source.parent != target.parent or source.stem != target.stem:
        raise ValueError(
            "formal DOCX and PDF must have the same directory and stem"
        )
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing PDF: {target}")
    return source, target


def _write_isolated_wps_profile(home: Path) -> None:
    config = home / ".config/Kingsoft/Office.conf"
    config.parent.mkdir(parents=True)
    config.write_text(
        "\n".join(
            (
                "[6.0]",
                (
                    "wpsoffice\\Application%20Settings\\"
                    "AppComponentMode=prome_independ"
                ),
                (
                    "wpsoffice\\Application%20Settings\\"
                    "AppComponentModeInstall=prome_independ"
                ),
                "common\\AcceptedEULA=true",
                "",
            )
        ),
        encoding="utf-8",
    )


def _worker(source: Path, target: Path) -> None:
    """Run WPS RPC in a disposable process; its extension crashes on teardown."""
    status = 1
    document = application = None
    try:
        from pywpsrpc.common import QtApp
        from pywpsrpc.rpcwpsapi import createWpsRpcInstance, wpsapi

        qt_app = QtApp(sys.argv)
        hr, rpc = createWpsRpcInstance()
        _require_ok(hr, "create WPS RPC instance")
        hr, application = rpc.getWpsApplication()
        _require_ok(hr, "get WPS application")
        application.Visible = False
        hr, document = application.Documents.Open(
            str(source),
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=False,
        )
        _require_ok(hr, "open DOCX in WPS")
        hr = document.ExportAsFixedFormat(
            str(target),
            wpsapi.wdExportFormatPDF,
            OpenAfterExport=False,
            IncludeDocProps=True,
            CreateBookmarks=wpsapi.wdExportCreateHeadingBookmarks,
        )
        _require_ok(hr, "export WPS PDF")
        status = 0
    except Exception as exc:
        print(f"WPS_RPC_ERROR={exc}", file=sys.stderr, flush=True)
    finally:
        if document is not None:
            try:
                document.Close(wpsapi.wdDoNotSaveChanges)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit(wpsapi.wdDoNotSaveChanges)
            except Exception:
                pass
        if status == 0:
            print("WPS_RPC_WORKER_OK", flush=True)
        # pywpsrpc 2.4.0 can segfault while its extension is torn down.
        os._exit(status)


def export_wps_pdf(source: Path, target: Path) -> dict[str, object]:
    source, target = _validate_paths(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="valvue-wps-rpc-") as raw_home:
        isolated_home = Path(raw_home)
        _write_isolated_wps_profile(isolated_home)
        env = os.environ.copy()
        env["HOME"] = str(isolated_home)
        env["XDG_CONFIG_HOME"] = str(isolated_home / ".config")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(source),
                    str(target),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            target.unlink(missing_ok=True)
            raise RuntimeError("WPS RPC export timed out after 180 seconds") from exc
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"WPS RPC export failed: {detail}")
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("WPS RPC returned without creating a PDF")

    with fitz.open(target) as pdf:
        creator = str(pdf.metadata.get("creator") or "")
        page_count = pdf.page_count
    if "WPS" not in creator.upper() or page_count < 1:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"invalid WPS PDF: creator={creator!r}, pages={page_count}"
        )
    return {
        "status": "wps_exported",
        "pdf": str(target),
        "creator": creator,
        "page_count": page_count,
        "size_bytes": target.stat().st_size,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.worker:
        _worker(args.source.resolve(), args.target.resolve())
        return 0
    result = export_wps_pdf(args.source, args.target)
    for key, value in result.items():
        print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
