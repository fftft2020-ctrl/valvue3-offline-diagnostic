"""Real inference against anonymous generated text and a ruled table."""
from io import BytesIO
import json
from pathlib import Path
import urllib.request


def main():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    draw.text((70, 50), "VALVE A100", font=font, fill="black")
    for x in (70, 550, 1100):
        draw.line((x, 170, x, 560), fill="black", width=3)
    for y in (170, 300, 430, 560):
        draw.line((70, y, 1100, y), fill="black", width=3)
    for (x, y, text) in ((100, 200, "Parameter"), (580, 200, "Value"),
                          (100, 330, "Pressure"), (580, 330, "600 kPa"),
                          (100, 460, "Travel"), (580, 460, "25 mm")):
        draw.text((x, y), text, font=font, fill="black")
    content = BytesIO()
    image.save(content, format="PNG")
    boundary = "valvue-anonymous-smoke"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="anonymous.png"\r\n'
            'Content-Type: image/png\r\n\r\n').encode() + content.getvalue() + f"\r\n--{boundary}--\r\n".encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for endpoint in ("ocr", "document"):
        request = urllib.request.Request(f"http://127.0.0.1:8765/v1/{endpoint}", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with opener.open(request, timeout=600) as response:
            result = json.load(response)
        page = result["pages"][0]
        text = page["text"].upper()
        assert "A100" in text and "600" in text and "25" in text, f"{endpoint}: missing known text"
        assert result["degraded"] is False and result["device"] == "cpu"
        if endpoint == "document":
            assert page["tables"], "structured pipeline did not recognize the anonymous table"
        print(f"{endpoint}: real CPU inference passed")


if __name__ == "__main__":
    main()
