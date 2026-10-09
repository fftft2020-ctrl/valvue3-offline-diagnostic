"""CPU PaddleOCR adapter for the frozen Skill's localhost protocol.

Model construction is real and eager: readiness requires both text and table
pipelines. No fallback OCR, no canned predictions, no document logging.
"""
from email.parser import BytesParser
from email.policy import default
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from io import BytesIO
import json
import logging
from pathlib import Path
import tempfile
import threading
import time

MAX_UPLOAD = 25 * 1024 * 1024


class TableText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in ("tr", "p", "div"):
            self.parts.append("\n")


def plain_table(html):
    parser = TableText()
    parser.feed(html)
    return "".join(parser.parts).strip()


def normalize_result(result, structured):
    payload = result.json
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("PaddleOCR result is not a JSON object")
    raw = payload.get("res", payload)
    ocr = raw.get("overall_ocr_res", {}) if structured else raw
    texts, scores = ocr.get("rec_texts", []), ocr.get("rec_scores", [])
    confidence = sum(scores) / len(scores) if scores else None
    blocks = raw.get("parsing_res_list", []) if structured else [
        {"text": text, "confidence": scores[i] if i < len(scores) else None}
        for i, text in enumerate(texts)
    ]
    # Structured layout reading order, with table cell boundaries preserved.
    content = []
    for block in blocks if structured else []:
        text = str(block.get("block_content", ""))
        content.append(plain_table(text) if block.get("block_label") == "table" else text)
    text = "\n".join(content).strip() or "\n".join(texts)
    return {"text": text, "blocks": blocks, "tables": raw.get("table_res_list", []),
            "paddle_result": raw, "confidence": confidence}


class Engine:
    def __init__(self):
        from paddleocr import PaddleOCR, PPStructureV3
        common = dict(device="cpu", use_doc_orientation_classify=False,
                      use_doc_unwarping=False, use_textline_orientation=False,
                      text_detection_model_name="PP-OCRv5_server_det",
                      text_recognition_model_name="PP-OCRv5_server_rec")
        self.general = PaddleOCR(**common)
        self.document = PPStructureV3(**common, use_table_recognition=True,
                                      use_formula_recognition=False, use_seal_recognition=False,
                                      use_chart_recognition=False, use_region_detection=False)
        self.lock = threading.Lock()
        self.identity = {"paddleocr": version("paddleocr"), "paddlepaddle": version("paddlepaddle"),
                         "paddlex": version("paddlex"), "adapter": "valvue3-docker-v1",
                         "text_models": ["PP-OCRv5_server_det", "PP-OCRv5_server_rec"],
                         "document_pipeline": "PP-StructureV3"}
        # Record actual downloaded model bytes for tracing portable cache bundles.
        import hashlib
        root = Path.home()
        files = {}
        for path in sorted((root / ".paddlex").rglob("*")):
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                files[str(path.relative_to(root))] = digest.hexdigest()
        (root / "valvue-model-manifest.json").write_text(json.dumps(
            {"provider": self.identity, "files_sha256": files}, indent=2), encoding="utf-8")

    def predict(self, path, structured):
        started = time.monotonic()
        with self.lock:
            pipeline = self.document if structured else self.general
            results = list(pipeline.predict(str(path)))
            if len(results) != 1:
                raise ValueError("one image must produce exactly one page")
            page = normalize_result(results[0], structured)
        return {"pages": [page], "pipeline": "PP-StructureV3" if structured else "PP-OCRv5",
                "model_version": json.dumps(self.identity, sort_keys=True), "device": "cpu",
                "degraded": False, "elapsed_ms": round((time.monotonic() - started) * 1000)}


def uploaded_image(content_type, body):
    from PIL import Image
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
    if not message.is_multipart():
        raise ValueError("multipart/form-data required")
    parts = [part for part in message.iter_parts()
             if part.get_param("name", header="content-disposition") == "file"]
    if len(parts) != 1:
        raise ValueError("exactly one file field required")
    content = parts[0].get_payload(decode=True)
    image = Image.open(BytesIO(content))
    if image.format not in {"PNG", "JPEG"} or image.width * image.height > 40_000_000:
        raise ValueError("PNG/JPEG within 40 megapixels required")
    image.verify()
    return content


def handler_for(engine):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, payload):
            content = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.path != "/ready":
                self.reply(404, {"error": "unknown endpoint"})
            else:
                self.reply(200, {"ready": True, "provider": engine.identity})

        def do_POST(self):
            if self.path not in ("/v1/ocr", "/v1/document"):
                self.reply(404, {"error": "unknown endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_UPLOAD:
                    self.reply(413, {"error": "upload limit: 25 MiB"})
                    return
                self.connection.settimeout(60)
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("incomplete upload")
                content = uploaded_image(self.headers.get("Content-Type", ""), body)
            except Exception:
                self.reply(400, {"error": "invalid image upload"})
                return
            try:
                with tempfile.TemporaryDirectory(prefix="valvue-ocr-") as directory:
                    # Never use the caller's filename as a filesystem path.
                    path = Path(directory) / "page.png"
                    path.write_bytes(content)
                    result = engine.predict(path, self.path == "/v1/document")
                self.reply(200, result)
            except Exception:
                logging.error("OCR inference failed; prediction unavailable")
                self.reply(503, {"error": "OCR inference failed"})

        def log_message(self, fmt, *args):
            pass # No client filenames, text or request bodies in access logs.

    return Handler


def main():
    # Initialization/download failures terminate the container; /ready is never faked.
    engine = Engine()
    print("Both CPU OCR pipelines loaded; listening on loopback:8765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), handler_for(engine)).serve_forever()


if __name__ == "__main__":
    main()
