from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import sys


SKILL_DIR = Path(__file__).resolve().parents[2]
RUNTIME_REQUIREMENTS = {
    "Pillow": "12.2.0",
    "PyMuPDF": "1.27.2.3",
    "python-docx": "1.2.0",
    "docxcompose": "2.2.0",
    "pywpsrpc": "2.4.0",
}
TEST_REQUIREMENTS = {"pytest": "9.0.3"}


def _locked_requirements(path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-r "):
            continue
        package, separator, locked_version = line.partition("==")
        assert separator == "=="
        requirements[package] = locked_version
    return requirements


def test_runtime_environment_is_explicit_and_current() -> None:
    assert sys.version_info >= (3, 11)
    assert _locked_requirements(
        SKILL_DIR / "requirements.txt"
    ) == RUNTIME_REQUIREMENTS
    assert all(
        version(package) == locked_version
        for package, locked_version in RUNTIME_REQUIREMENTS.items()
    )


def test_test_environment_extends_runtime_requirements() -> None:
    test_path = SKILL_DIR / "requirements-test.txt"
    lines = test_path.read_text(encoding="utf-8").splitlines()

    assert "-r requirements.txt" in lines
    assert _locked_requirements(test_path) == TEST_REQUIREMENTS
    assert all(
        version(package) == locked_version
        for package, locked_version in TEST_REQUIREMENTS.items()
    )
