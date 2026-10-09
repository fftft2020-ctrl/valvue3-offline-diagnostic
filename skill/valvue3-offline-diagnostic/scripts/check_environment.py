#!/usr/bin/env python3
"""Check local dependencies without submitting a model request or changing configuration."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from offline_engine.distribution import SKILL_DIR, runtime_identity, verify_package


def _command(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        return result.returncode == 0, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def check_environment() -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}

    def record(name: str, passed: bool, detail: str, affects: list[str]) -> None:
        checks[name] = {"status": "passed" if passed else "missing", "detail": detail, "affects": affects}

    record("platform", sys.platform == "linux", "首版运行目标为 Linux", ["diagnosis", "wps_pdf"])
    record("python", sys.version_info >= (3, 11), sys.version.split()[0], ["docx", "diagnosis", "ocr", "wps_pdf"])
    try:
        checks["package"] = {**verify_package(), "affects": ["docx", "diagnosis", "ocr", "wps_pdf"]}
        identity = runtime_identity()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record("package", False, str(exc), ["docx", "diagnosis", "ocr", "wps_pdf"])
        identity = {}
    for line in (SKILL_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        package, expected = line.split("==", 1)
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "unavailable"
        affects = ["wps_pdf"] if package == "pywpsrpc" else ["docx", "diagnosis", "ocr", "wps_pdf"]
        record(package, installed == expected, f"installed={installed}; required={expected}", affects)
    imports_ok, _ = _command([sys.executable, "-c", "import fitz, PIL, docx, docxcompose"])
    record("python_imports", imports_ok, "导入 PDF、图像和 DOCX 模块", ["docx", "diagnosis", "ocr", "wps_pdf"])
    for tool, affects in (("curl", ["ocr"]), ("pdftotext", []), ("wps", ["wps_pdf"]),
                          ("bwrap", ["diagnosis"]), ("libreoffice", [])):
        path = shutil.which(tool) or (shutil.which("soffice") if tool == "libreoffice" else None)
        record(tool, bool(path), path or "unavailable", affects)
    record("display", bool(os.environ.get("DISPLAY")), "WPS 桌面显示环境", ["wps_pdf"])
    codex = None
    try:
        import run_metered_codex
        codex = run_metered_codex._codex_executable()
        ok, output = _command([codex, "--version"])
        record("codex", ok, output if ok else "CLI 无法启动", ["diagnosis"])
        ok, _ = _command([codex, "login", "status"])
        record("codex_login", ok, "仅检查本机登录状态，不输出认证信息", ["diagnosis"])
        ok, output = _command([codex, "exec", "--help"])
        flags = ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema")
        record("codex_flags", ok and all(flag in output for flag in flags),
               "诊断所需 CLI 参数", ["diagnosis"])
    except (OSError, RuntimeError, ValueError):
        record("codex", False, "Codex CLI unavailable", ["diagnosis"])
    auth_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    record("codex_auth_file", (auth_root / "auth.json").is_file(),
           "隔离调用需要 auth.json；不读取或导出认证内容", ["diagnosis"])
    cached = False
    try:
        catalog = json.loads((auth_root / "models_cache.json").read_text(encoding="utf-8"))
        cached = any(model.get("slug") == "gpt-6-sol" for model in catalog.get("models", []) if isinstance(model, dict))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    checks["model_access"] = {"status": "unverified", "cached_model_present": cached,
                              "detail": "未请求模型；缓存出现 gpt-6-sol 不证明当前访问权限", "affects": ["diagnosis"]}
    if codex and shutil.which("bwrap") and (auth_root / "auth.json").is_file():
        try:
            with tempfile.TemporaryDirectory(prefix="valvue-env-") as directory:
                root = Path(directory)
                evidence = root / "evidence.txt"
                denied = root / "excluded.txt"
                evidence.write_text("anonymous environment probe", encoding="utf-8")
                denied.write_text("must remain hidden", encoding="utf-8")
                output = root / "output"
                output.mkdir()
                payload = {"files": [{"path": str(evidence), "sha256": run_metered_codex._sha256(evidence)}],
                           "denied_paths": [str(denied), str(auth_root / "sessions"), str(auth_root / "history.jsonl")]}
                prefix, env = run_metered_codex._isolation_prefix(payload, output, codex)
                run_metered_codex._check_isolation(prefix, env, payload, codex, timeout=15)
            record("isolation", True, "证据只读、排除文件不可见、CLI 可在隔离中启动", ["diagnosis"])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            record("isolation", False, "证据隔离实测失败；检查 bwrap、用户命名空间及 CLI 运行依赖", ["diagnosis"])
    else:
        record("isolation", False, "缺少 CLI、bwrap 或 auth.json，未执行隔离实测", ["diagnosis"])
    ocr_detail = "仅检查本机 OCR /ready；未验证实际识别结果"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8765/ready", timeout=3) as response:
            ready = json.loads(response.read(65536))
        ok = isinstance(ready, dict) and ready.get("ready") is True
    except (OSError, ValueError) as exc:
        ok = False
        ocr_detail += f"；探针失败：{type(exc).__name__}"
    record("ocr_service", ok, ocr_detail, ["ocr"])
    readiness = {}
    for stage in ("docx", "diagnosis", "ocr", "wps_pdf"):
        missing = [name for name, item in checks.items() if item["status"] == "missing" and stage in item["affects"]]
        readiness[stage] = {"status": "blocked" if missing else "prerequisites_available", "missing": missing}
    return {"status": "partial" if any(item["status"] == "blocked" for item in readiness.values()) else "prerequisites_available",
            "runtime": identity, "checks": checks, "stages": readiness,
            "unverified": ["远端模型访问及实际诊断", "OCR 实际识别", "WPS 导出、分页与交付验收"]}


def main() -> int:
    result = check_environment()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["status"] == "partial")


if __name__ == "__main__":
    raise SystemExit(main())
