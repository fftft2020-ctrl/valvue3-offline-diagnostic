"""Packaging boundaries and HTTP protocol, without mocking model acceptance."""
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ocr_adapter", ROOT / "docker/ocr_server.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def multipart(content, filename="../../must-not-be-used.png"):
    boundary = "test-boundary"
    header = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
              'Content-Type: image/png\r\n\r\n').encode()
    return f"multipart/form-data; boundary={boundary}", header + content + f"\r\n--{boundary}--\r\n".encode()


def png():
    stream = BytesIO()
    Image.new("RGB", (10, 10), "white").save(stream, format="PNG")
    return stream.getvalue()


class Prediction:
    def __init__(self, payload):
        self.json = {"res": payload}


def test_table_values_and_raw_structure_survive_adapter():
    html = "<table><tr><td>Pressure</td><td>600 &amp; 700 kPa</td></tr><tr><td>Travel</td><td>25 mm</td></tr></table>"
    payload = {"parsing_res_list": [{"block_label": "table", "block_content": html}],
               "table_res_list": [{"pred_html": html}],
               "overall_ocr_res": {"rec_texts": ["Pressure", "600 & 700 kPa"], "rec_scores": [0.8, 1.0]}}
    page = adapter.normalize_result(Prediction(payload), True)
    assert page["text"] == "Pressure\t600 & 700 kPa\t\nTravel\t25 mm"
    assert page["tables"] == payload["table_res_list"]
    assert page["paddle_result"] == payload
    assert page["confidence"] == pytest.approx(0.9)


def test_general_ocr_does_not_require_layout_or_invent_confidence():
    page = adapter.normalize_result(Prediction({"rec_texts": ["A100", "600 kPa"]}), False)
    assert page["text"] == "A100\n600 kPa"
    assert page["confidence"] is None
    assert page["tables"] == []


def test_upload_requires_valid_image_and_preserves_binary_bytes():
    content = png()
    content_type, body = multipart(content)
    assert adapter.uploaded_image(content_type, body) == content
    with pytest.raises(Exception):
        content_type, body = multipart(b"not an image")
        adapter.uploaded_image(content_type, body)
    with pytest.raises(ValueError):
        adapter.uploaded_image("application/json", b"{}")


@pytest.fixture
def endpoint():
    class TestEngine:
        identity = {"adapter": "protocol-test-double"}

        def __init__(self):
            self.calls = []
            self.fail = False

        def predict(self, path, structured):
            assert path.name == "page.png"
            assert path.read_bytes() == png()
            self.calls.append((path, structured))
            if self.fail:
                raise RuntimeError("private document text must not appear in response")
            return {"pages": [{"text": "protocol test only"}]}

    engine = TestEngine()
    server = adapter.ThreadingHTTPServer(("127.0.0.1", 0), adapter.handler_for(engine))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}", engine
    server.shutdown()
    server.server_close()
    worker.join()


def request(base, path, body=None, content_type=None):
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": content_type} if content_type else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as response:
        return json.load(response)


@pytest.mark.parametrize("path,structured", [("/v1/ocr", False), ("/v1/document", True)])
def test_real_http_multipart_routes_and_removes_temporary_image(endpoint, path, structured):
    base, engine = endpoint
    assert request(base, "/ready")["ready"] is True
    content_type, body = multipart(png())
    assert request(base, path, body, content_type)["pages"][0]["text"] == "protocol test only"
    temporary, actual = engine.calls[-1]
    assert actual is structured
    assert not temporary.exists()


def test_http_failure_is_not_success_or_leaked_private_text(endpoint):
    base, engine = endpoint
    content_type, body = multipart(png())
    engine.fail = True
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, "/v1/document", body, content_type)
    assert exc.value.code == 503
    assert json.load(exc.value) == {"error": "OCR inference failed"}
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, "/v1/ocr", b"bad image", content_type)
    assert exc.value.code == 400


def test_exact_build_context_excludes_future_credentials_and_customer_files():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert lines[1] == "**"
    included = [line[1:] for line in lines if line.startswith("!")]
    assert all("*" not in line for line in included)
    skill_files = {path.relative_to(ROOT).as_posix() for path in (ROOT / "skill").rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts}
    assert skill_files == {line for line in included if line.startswith("skill/") and not line.endswith("/")}
    assert all(not line.startswith(("projects/", ".runtime/", ".codex/", ".env")) for line in included)


def test_entrypoint_keeps_argument_boundaries_and_does_not_claim_display(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    fake_python = binary / "python"
    fake_python.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\nprintf 'DISPLAY=%s\\n' \"${DISPLAY-unset}\"\n")
    fake_python.chmod(0o755)
    import os
    env = {**os.environ, "PATH": f"{binary}:/usr/bin:/bin", "SKILL_DIR": "/safe/skill", "DISPLAY": ":99",
           "VALVUE_START_XVFB": "0"}
    result = subprocess.run(["bash", str(ROOT / "docker/entrypoint.sh"), "report", "--project", "/a path/project"],
                            env=env, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ["/safe/skill/scripts/run_offline_report.py", "--project",
                                          "/a path/project", "DISPLAY=unset"]


def test_missing_wps_does_not_change_skill_release():
    # Additions stay outside the frozen package; the original checker hashes all files.
    import sys
    sys.path.insert(0, str(ROOT / "skill/valvue3-offline-diagnostic/scripts"))
    from offline_engine.distribution import verify_package
    assert verify_package()["status"] == "verified"


def test_bundle_is_portable_and_excludes_sender_auth_and_projects(tmp_path):
    import os
    import shutil
    import sys
    import tarfile
    source = tmp_path / "sender"
    source.mkdir()
    for name in ("compose.yaml", "compose.isolation.yaml", ".env.example"):
        shutil.copy(ROOT / name, source)
    shutil.copytree(ROOT / "docker", source / "docker")
    shutil.copytree(ROOT / "docs", source / "docs")
    (source / ".env").write_text("PRIVATE_SECRET=do-not-export\n")
    (source / "projects").mkdir()
    (source / "projects/client.txt").write_text("private client original")
    (source / ".runtime/codex").mkdir(parents=True)
    (source / ".runtime/codex/auth.json").write_text("private authentication")
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(f"#!{sys.executable}\n" + r'''
import io,json,sys,tarfile
a=sys.argv[1:]
if a[:2]==["compose","config"]:
 print(json.dumps({"services":{"diagnostic":{"image":"private-runtime:v7"},"ocr":{"image":"private-ocr:v7"}}}))
elif a[:2]==["image","save"]:
 sys.stdout.buffer.write(b"anonymous image archive test double")
elif a[:2]==["compose","run"]:
 with tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz") as t:
  b=b"anonymous model cache"; info=tarfile.TarInfo("model.bin"); info.size=len(b); t.addfile(info,io.BytesIO(b))
elif a[:2] in (["compose","up"],["compose","version"],["image","load"]):
 pass
else: raise SystemExit("unexpected Docker command: "+repr(a))
''')
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"}
    subprocess.run(["bash", "docker/package.sh", "bundle"], cwd=source, env=env,
                   capture_output=True, text=True, check=True)
    bundle = source / "bundle"
    assert "private-runtime:v7" in (bundle / ".env.example").read_text()
    assert not (bundle / ".env").exists()
    assert not (bundle / "projects").exists()
    assert not (bundle / ".runtime").exists()
    with tarfile.open(bundle / "models.tar.gz") as archive:
        assert archive.getnames() == ["model.bin"]
    subprocess.run(["bash", "docker/import.sh"], cwd=bundle, env=env,
                   capture_output=True, text=True, check=True)
    assert (bundle / ".runtime/models/model.bin").read_bytes() == b"anonymous model cache"
    assert list((bundle / ".runtime/codex").iterdir()) == []
