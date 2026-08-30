# Dashboard-Image fuer das oeffentliche Deployment.
#
# Unterschied zum Dockerfile im Projektroot: dort steckt die komplette
# Pipeline drin (Tesseract-OCR fuer den House-PDF-Extractor, Chromium fuer den
# Senate-Scraper — zusammen ~600 MB). Die oeffentliche Instanz zeigt nur das
# Dashboard und laedt einen fertigen Datenstand; sie scrapt und importiert
# nichts. Deshalb faellt beides hier weg: kleineres Image, schnellerer Build.
FROM python:3.12-slim

WORKDIR /app

# Python-Pakete in eigener Layer (Docker cached sie, solange sich
# requirements.txt nicht aendert).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Projektcode. data/ und logs/ sind per .dockerignore ausgeschlossen und
# kommen zur Laufzeit als Volume dazu (s. deploy/docker-compose.prod.yml).
COPY . .

EXPOSE 8501

# Streamlit meldet unter /_stcore/health, ob der Server bereit ist. Docker
# startet den Container neu, wenn das dreimal hintereinander fehlschlaegt.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health').read()"

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
