# Congress Trade Tracker — Docker-Image
# Basis: schlankes Debian mit Python 3.12
FROM python:3.12-slim

# System-Pakete: Tesseract OCR (House PDF Extractor) + Basis-Lib fuer OpenCV-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-Pakete zuerst installieren (eigene Layer -> Docker cached sie,
# solange sich requirements.txt nicht aendert -> schnellere Re-Builds).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + dessen System-Abhaengigkeiten fuer den Senate-Scraper (Playwright).
# Laeuft nur, wenn der Scraper aktiv ist — kein Verbrauch im Leerlauf.
RUN playwright install --with-deps chromium

# Restlichen Projektcode hineinkopieren (data/, logs/, venv/, archive/ sind
# per .dockerignore ausgeschlossen).
COPY . .

# Streamlit-Dashboard-Port
EXPOSE 8501

# Standardstart: Dashboard. address=0.0.0.0 macht es ausserhalb des Containers erreichbar.
CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
