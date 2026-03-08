"""Analyse-Skript: Extrahiert alle Wörter mit x0, top, size, fontname, text
aus den Seiten 1-3, 20-21, 40-47 des Senate-PDFs."""

import pdfplumber
import json

PDF_PATH = r"c:\Users\benedikt\OneDrive\bmb_trdprg\Zwischenlager\us_senate_pdf_parsing_tool\us_senat_(sub)committees_all_pdf.pdf"

# Seiten (0-indexed): 0-2, 19-20, 39-46
PAGES = list(range(0, 3)) + list(range(19, 21)) + list(range(39, 47))

with pdfplumber.open(PDF_PATH) as pdf:
    print(f"Gesamt-Seiten im PDF: {pdf.pages.__len__()}")
    print(f"Analysierte Seiten (1-indexed): {[p+1 for p in PAGES]}")
    print("=" * 100)

    for page_idx in PAGES:
        if page_idx >= len(pdf.pages):
            print(f"\n--- SEITE {page_idx+1} existiert nicht (nur {len(pdf.pages)} Seiten) ---")
            continue
        page = pdf.pages[page_idx]
        words = page.extract_words(extra_attrs=["fontname", "size"])

        print(f"\n{'='*100}")
        print(f"SEITE {page_idx + 1} (Breite: {page.width}, Höhe: {page.height})")
        print(f"{'='*100}")
        print(f"{'x0':>8} {'top':>8} {'size':>6} {'fontname':<35} text")
        print(f"{'-'*8} {'-'*8} {'-'*6} {'-'*35} {'-'*40}")

        for w in words:
            print(f"{w['x0']:8.1f} {w['top']:8.1f} {w['size']:6.1f} {w['fontname']:<35} {w['text']}")
