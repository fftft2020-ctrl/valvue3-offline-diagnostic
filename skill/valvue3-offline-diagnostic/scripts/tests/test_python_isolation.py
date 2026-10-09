"""Anonymous runtime-layout and fail-closed isolation regressions."""
from pathlib import Path
import json
import subprocess
import sys

import pytest
import run_metered_codex as meter


def _prefixes(monkeypatch, prefix, base=None, exec_prefix=None, base_exec=None):
    for name, value in {"prefix": prefix, "exec_prefix": exec_prefix or prefix,
                        "base_prefix": base or prefix,
                        "base_exec_prefix": base_exec or base or prefix}.items():
        monkeypatch.setattr(meter.sys, name, str(value))


@pytest.mark.parametrize("venv", [False, True])
def test_non_usr_python_mounts_base_runtime(tmp_path, monkeypatch, venv):
    base = tmp_path / "python"
    base.mkdir()
    prefix = tmp_path / "venv" if venv else base
    prefix.mkdir(exist_ok=True)
    _prefixes(monkeypatch, prefix, base)
    assert meter._python_runtime_roots({}) == list(dict.fromkeys([prefix, base]))
    assert tmp_path not in meter._python_runtime_roots({})


def test_split_exec_prefix_is_included(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("venv", "exec", "base", "base-exec")]
    for root in roots:
        root.mkdir()
    _prefixes(monkeypatch, *[roots[0], roots[2], roots[1], roots[3]])
    assert meter._python_runtime_roots({}) == roots


def test_usr_runtime_uses_existing_mount(monkeypatch):
    _prefixes(monkeypatch, Path('/usr'))
    assert meter._python_runtime_roots({}) == []


def test_symlinked_runtime_keeps_both_paths(tmp_path, monkeypatch):
    base = tmp_path / "real-python"
    base.mkdir()
    alias = tmp_path / "python-link"
    alias.symlink_to(base, target_is_directory=True)
    _prefixes(monkeypatch, alias)
    assert meter._python_runtime_roots({}) == [alias, base]


@pytest.mark.parametrize("value", ['/', '/opt', '/home', '/tmp', '/workspace'])
def test_broad_roots_fail_closed(monkeypatch, value):
    _prefixes(monkeypatch, Path(value))
    with pytest.raises(RuntimeError, match="unsafe Python runtime root"):
        meter._python_runtime_roots({})


@pytest.mark.parametrize("protected", ['home', 'auth', 'denied'])
def test_runtime_cannot_expose_protected_data(tmp_path, monkeypatch, protected):
    root = tmp_path / "runtime"
    root.mkdir()
    _prefixes(monkeypatch, root)
    payload = {}
    if protected == 'home':
        monkeypatch.setenv('HOME', str(root / 'home'))
    elif protected == 'auth':
        monkeypatch.setenv('CODEX_HOME', str(root / 'identity'))
    else:
        payload['denied_paths'] = [str(root / 'answer.txt')]
    with pytest.raises(RuntimeError, match="unsafe Python runtime root"):
        meter._python_runtime_roots(payload)


def test_missing_runtime_fails_closed(tmp_path, monkeypatch):
    _prefixes(monkeypatch, tmp_path / "missing")
    with pytest.raises(RuntimeError, match="missing Python runtime root"):
        meter._python_runtime_roots({})


@pytest.mark.parametrize("interpreter", ['current', 'base'])
def test_real_python_namespace_without_model(tmp_path, interpreter):
    """Exercise encodings, stdlib, read-only evidence and excluded siblings."""
    if not meter.shutil.which('bwrap'):
        pytest.skip('namespace integration requires bwrap')
    python = sys.executable if interpreter == 'current' else sys._base_executable
    script = r'''
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import run_metered_codex as m
root = pathlib.Path(sys.argv[2])
evidence = root / 'input.txt'; evidence.write_text('anonymous')
secret = root / 'excluded.txt'; secret.write_text('excluded')
auth = root / 'empty-auth.json'; auth.write_text('{}')
output = root / 'output'; output.mkdir()
original = m._require_file
m._require_file = lambda path, label: auth if label == 'Codex authentication' else original(path, label)
payload = {'files': [{'path': str(evidence), 'sha256': m._sha256(evidence)}], 'denied_paths': [str(secret)]}
prefix, env = m._isolation_prefix(payload, output, sys.executable)
print(json.dumps(m._check_isolation(prefix, env, payload, sys.executable, timeout=20)))
'''
    result = subprocess.run([python, '-c', script, str(Path(meter.__file__).parent), str(tmp_path)],
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    check = json.loads(result.stdout)
    assert check['inputs_read_only'] is True
    assert check['symlink_escape'] is False
    assert check['status'] == 'passed'


@pytest.mark.parametrize("protected", ['auth', 'denied'])
def test_runtime_cannot_be_inside_protected_data(tmp_path, monkeypatch, protected):
    root = tmp_path / "private" / "runtime"
    root.mkdir(parents=True)
    _prefixes(monkeypatch, root)
    payload = {}
    if protected == 'auth':
        monkeypatch.setenv('CODEX_HOME', str(root.parent))
    else:
        payload['denied_paths'] = [str(root.parent)]
    with pytest.raises(RuntimeError, match="unsafe Python runtime root"):
        meter._python_runtime_roots(payload)


@pytest.mark.parametrize("protected", ['auth', 'denied'])
def test_symlink_cannot_disguise_protected_runtime(tmp_path, monkeypatch, protected):
    private = tmp_path / 'private'
    private.mkdir()
    alias = tmp_path / 'python'
    alias.symlink_to(private, target_is_directory=True)
    _prefixes(monkeypatch, alias)
    payload = {}
    if protected == 'auth':
        monkeypatch.setenv('CODEX_HOME', str(private))
    else:
        payload['denied_paths'] = [str(private)]
    with pytest.raises(RuntimeError, match='unsafe Python runtime root'):
        meter._python_runtime_roots(payload)
