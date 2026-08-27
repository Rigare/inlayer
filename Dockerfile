# Mehrstufig: `base` haelt die Laufzeitabhaengigkeiten, `test` faehrt die Suite
# im Image (docker build --target test .), `runtime` ist das Deploy-Ziel.
FROM python:3.13-slim AS base

WORKDIR /app

# Systemabhängigkeiten fuer pymeshfix / manifold3d
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Abhaengigkeiten zuerst installieren (besserer Layer-Cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Teststage --------------------------------------------------------------
# Laeuft die Suite waehrend des Builds; ein roter Test bricht den Build ab.
# Nicht Teil von `runtime`, tests/ landen also nie im Deploy-Image.
FROM base AS test

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini inlayer.py app.py app_helpers.py i18n.py ./
COPY tests/ ./tests/
RUN python -m pytest -q


# --- Laufzeit ---------------------------------------------------------------
FROM base AS runtime

# Anwendungscode kopieren
COPY inlayer.py app.py app_helpers.py i18n.py ./
# Streamlit-Konfiguration: das Custom-CSS in app.py ist auf das dunkle Theme
# ausgelegt. Ohne diese Datei faellt der Container auf das helle Standard-Theme
# zurueck und Sidebar-Beschriftungen werden unlesbar.
COPY .streamlit/ ./.streamlit/

# Nicht als root laufen: der Container haengt am offenen Port und verarbeitet
# hochgeladene Dateien. --create-home, weil Streamlit ein beschreibbares HOME
# fuer seinen Config-/Cache-Ordner erwartet.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
