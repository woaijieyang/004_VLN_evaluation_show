"""Download the OFL font into plugin-owned cache and verify pinned content."""
import hashlib
from pathlib import Path
import urllib.request
folder=Path(__file__).resolve().parents[2]/".assets"
folder.mkdir(exist_ok=True)
font=folder/"NotoSansCJKsc-Regular.otf"
expected="2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
url="https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
if not font.exists():
    with urllib.request.urlopen(url,timeout=60) as response:
        data=response.read()
    if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError("Font SHA mismatch")
    font.write_bytes(data)
if hashlib.sha256(font.read_bytes()).hexdigest()!=expected:raise RuntimeError("Font SHA mismatch")
license_file=folder/"OFL.txt"
if not license_file.exists():
    with urllib.request.urlopen("https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE",timeout=30) as response:
        license_file.write_bytes(response.read())
print(font)
