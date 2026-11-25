"""Rebuild samples/scans: image-only copies of five sample invoices.

Each page is rendered to an image, turned grey, rotated a little, blurred and saved as
a JPEG inside a new PDF, so there is no text layer left to read. That is what a
scanner or a phone camera produces, and it is the case a text-only extractor cannot
handle.

macOS only: the page is rendered with Quick Look (qlmanage). The results are committed,
so nobody needs to run this to use or test the project.

Usage: python tools/make_scans.py [SAMPLES_DIR] [OUT_DIR]
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageFilter

FILES = ("eu-01.pdf", "freelance-01.pdf", "pk-01.pdf", "uk-02.pdf", "us-03.pdf")  # one per layout
A4 = (595.28, 841.89)  # points


def scan(pdf: Path, out: Path, angle: float) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", tmp, str(pdf)], check=True, capture_output=True)
        rendered = Image.open(next(Path(tmp).glob("*.png"))).convert("RGBA")
        page = Image.alpha_composite(Image.new("RGBA", rendered.size, "white"), rendered).convert("L")
    page = page.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    page = page.filter(ImageFilter.GaussianBlur(0.7))
    jpeg = io.BytesIO()
    page.save(jpeg, "JPEG", quality=50)
    jpeg.seek(0)

    document = FPDF(unit="pt", format=A4)
    document.set_margin(0)
    document.set_auto_page_break(False)
    document.add_page()
    document.image(jpeg, x=0, y=0, w=A4[0], h=A4[1])
    out.write_bytes(bytes(document.output()))


def main(argv: list[str]) -> int:
    samples = Path(argv[1]) if len(argv) > 1 else Path("samples")
    out = Path(argv[2]) if len(argv) > 2 else samples / "scans"
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads((samples / "ground_truth.json").read_text())
    for index, name in enumerate(FILES):
        scan(samples / name, out / name, angle=1.3 if index % 2 == 0 else -1.1)
    (out / "ground_truth.json").write_text(json.dumps({name: truth[name] for name in FILES}, indent=2) + "\n")
    print(f"Wrote {len(FILES)} scans and ground_truth.json to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
