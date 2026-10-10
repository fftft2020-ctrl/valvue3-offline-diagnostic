"""Real evidence isolation and CLI launch, using disposable dummy auth.

Never submits a model request or reads the user's actual authentication.
"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile


def main():
    sys.path.insert(0, str(Path(os.environ["SKILL_DIR"]) / "scripts"))
    import run_metered_codex
    with tempfile.TemporaryDirectory(prefix="valvue-isolation-") as directory:
        root = Path(directory)
        auth = root / "auth"
        auth.mkdir()
        (auth / "auth.json").write_text("{}")
        os.environ["CODEX_HOME"] = str(auth)
        evidence, excluded = root / "evidence.txt", root / "excluded.txt"
        evidence.write_text("anonymous isolation probe")
        excluded.write_text("must not be visible")
        output = root / "output"
        output.mkdir()
        payload = {"files": [{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
                   "denied_paths": [str(excluded), str(auth / "sessions"), str(auth / "history.jsonl")]}
        codex = run_metered_codex._codex_executable()
        prefix, env = run_metered_codex._isolation_prefix(payload, output, codex)
        print(run_metered_codex._check_isolation(prefix, env, payload, codex, timeout=30))


if __name__ == "__main__":
    main()
