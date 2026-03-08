"""
Extrahiert Bilder aus gescannten PDFs und speichert sie als PNG-Dateien.
"""
import io
import os
import sys
import requests
import pdfplumber
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PDFS_TO_ANALYZE = [
    ("8220764", "Adrian_Smith"),
    ("8221310", "Harold_Dallas_Rogers"),
    ("9115670", "Chuck_Fleischmann"),
    ("8220747", "Gus_Bilirakis"),
    ("8221228", "Michael_McCaul"),
]

BASE_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/{doc_id}.pdf"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "debug", "fehler_pdf_images")


def extract_images(doc_id: str, name: str):
    url = BASE_URL.format(doc_id=doc_id)
    print(f"Downloading {doc_id} ({name})...")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for i, page in enumerate(pdf.pages):
            if page.images:
                # Render the full page as image
                pil_image = page.to_image(resolution=200).original
                out_path = os.path.join(OUTPUT_DIR, f"{doc_id}_{name}_page{i+1}.png")
                pil_image.save(out_path)
                print(f"  Saved: {out_path}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for doc_id, name in PDFS_TO_ANALYZE:
        try:
            extract_images(doc_id, name)
        except Exception as e:
            print(f"  ERROR {doc_id}: {e}")

    print(f"\nDone. Images saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
