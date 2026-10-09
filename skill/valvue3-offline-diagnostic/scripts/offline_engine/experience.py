"""The sole offline experience source: complete, human-readable prose."""
from pathlib import Path

COMMON_EXPERIENCE_PATH = Path(__file__).resolve().parents[2] / "references/common-offline-experience.md"


class ExperienceError(ValueError):
    """The offline experience text is missing or has the wrong domain."""


def load_offline_experience(path: Path = COMMON_EXPERIENCE_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExperienceError(f"Cannot read offline experience: {path}") from exc
    if not text.startswith("# ValVue3 离线诊断通用经验\n") or not text.partition("\n")[2].strip():
        raise ExperienceError(f"Expected non-empty ValVue3 offline experience prose: {path}")
    return text
