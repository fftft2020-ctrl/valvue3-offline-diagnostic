from pathlib import Path
import pytest
from offline_engine.experience import ExperienceError, load_offline_experience, COMMON_EXPERIENCE_PATH


def test_offline_prose_is_complete_and_is_the_only_runtime_library():
    text = load_offline_experience()
    assert text == COMMON_EXPERIENCE_PATH.read_text(encoding="utf-8")
    assert "更换定位器" in text
    assert "反证" in text
    assert not COMMON_EXPERIENCE_PATH.with_suffix(".json").exists()
    assert not any(name in text for name in ("盛虹", "中天", "恒力", "红四方", "common-step-"))


@pytest.mark.parametrize("body", ["", "# 在线诊断经验\n正文", "# ValVue3 离线诊断通用经验\n"])
def test_loader_rejects_empty_or_wrong_domain(tmp_path: Path, body: str):
    path = tmp_path / "experience.md"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ExperienceError):
        load_offline_experience(path)


def test_loader_requires_the_source(tmp_path: Path):
    with pytest.raises(ExperienceError, match="Cannot read"):
        load_offline_experience(tmp_path / "missing.md")
