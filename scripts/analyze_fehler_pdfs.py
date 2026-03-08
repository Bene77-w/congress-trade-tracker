"""
Analyse-Skript für fehlgeschlagene House-PDFs (hand-delivered reports).
Untersucht PDF-Struktur: Textlayer, Bilder, Seitenanzahl, etc.
"""
import io
import os
import sys
import requests
import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Die 5 einzigartigen PDFs aus schaue_kurz_hier_rein.txt
PDFS_TO_ANALYZE = [
    ("8220764", "Adrian Smith"),
    ("8221310", "Harold Dallas Rogers"),
    ("9115670", "Chuck Fleischmann"),
    ("8220747", "Gus M. Bilirakis"),
    ("8221228", "Michael T. McCaul"),
]

BASE_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/{doc_id}.pdf"

def analyze_pdf(doc_id: str, name: str):
    """Analysiert eine einzelne PDF-Datei."""
    url = BASE_URL.format(doc_id=doc_id)
    print(f"\n{'='*70}")
    print(f"DocID: {doc_id} | Name: {name}")
    print(f"URL: {url}")
    print(f"{'='*70}")

    # Download
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        print(f"  FEHLER: HTTP {resp.status_code}")
        return

    pdf_bytes = resp.content
    print(f"  Dateigröße: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024:.1f} KB)")

    # pdfplumber Analyse
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        print(f"  Seitenanzahl: {len(pdf.pages)}")
        print(f"  Metadaten: {pdf.metadata}")

        for i, page in enumerate(pdf.pages):
            print(f"\n  --- Seite {i+1} ---")
            print(f"  Seitengröße: {page.width} x {page.height}")

            # Text extrahieren
            text = page.extract_text() or ""
            text_clean = text.replace("\x00", "").strip()
            print(f"  Text-Zeichen: {len(text_clean)}")
            if text_clean:
                # Erste 200 Zeichen anzeigen
                preview = text_clean[:200].replace('\n', ' | ')
                print(f"  Text-Preview: {preview}")
            else:
                print(f"  Text: LEER (kein Textlayer)")

            # Bilder prüfen
            images = page.images
            print(f"  Bilder gefunden: {len(images)}")
            for j, img in enumerate(images):
                print(f"    Bild {j+1}: x0={img.get('x0',0):.0f}, y0={img.get('top',0):.0f}, "
                      f"x1={img.get('x1',0):.0f}, y1={img.get('bottom',0):.0f}, "
                      f"width={img.get('width','?')}, height={img.get('height','?')}")

            # Tabellen prüfen
            tables = page.find_tables()
            print(f"  Tabellen gefunden: {len(tables)}")

            # Chars prüfen (individuelle Zeichen mit Position)
            chars = page.chars
            print(f"  Chars (positioned): {len(chars)}")
            if chars:
                # Stichprobe: erste 5 Zeichen
                for c in chars[:5]:
                    print(f"    Char: '{c.get('text','')}' at ({c.get('x0',0):.0f}, {c.get('top',0):.0f})")

    # Auch raw bytes prüfen (PDF-Interne Struktur)
    content_str = pdf_bytes[:2000].decode('latin-1', errors='replace')
    if '/Image' in content_str:
        print(f"\n  PDF enthält /Image Referenz in den ersten 2000 bytes")
    if '/Font' in content_str:
        print(f"  PDF enthält /Font Referenz in den ersten 2000 bytes")
    if '/XObject' in content_str:
        print(f"  PDF enthält /XObject Referenz in den ersten 2000 bytes")


def main():
    print("=" * 70)
    print("  ANALYSE: Hand-delivered House PTR PDFs")
    print("=" * 70)

    for doc_id, name in PDFS_TO_ANALYZE:
        try:
            analyze_pdf(doc_id, name)
        except Exception as e:
            print(f"  FEHLER bei {doc_id}: {e}")

    print(f"\n{'='*70}")
    print("  ANALYSE ABGESCHLOSSEN")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
