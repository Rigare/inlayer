# Mehrstufig: `base` haelt die Laufzeitabhaengigkeiten, `test` faehrt die Suite
# im Image (docker build --target test .), `runtime` ist das Deploy-Ziel.
FROM python:3.13-slim AS base

WORKDIR /app

# No compiler: every pinned requirement resolves as a cp313 wheel for x86_64
# and aarch64 (pip install --dry-run --only-binary=:all:), and build-essential
# added ~350 MB plus a toolchain to the internet-facing image. Should a
# source build ever be needed, it belongs in a builder stage of its own.
# libgl1/libglib2.0-0 stay deliberately (see AGENTS.md, CI section).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Abhaengigkeiten zuerst installieren (besserer Layer-Cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Test stage -------------------------------------------------------------
# Runs the suite during the build; a red test fails the build.
# Not part of `runtime`, so tests/ never end up in the deploy image.
FROM base AS test

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini inlayer.py inlayer_project.py app.py app_helpers.py i18n.py ./
COPY tests/ ./tests/
# tests/test_streamlit_config.py reads all of these: the config whose theme and
# upload limit it pins down, the Dockerfile itself to verify the runtime
# stage's COPY line, and compose.yaml for its localhost-only port. Without them
# those tests fail inside the image only - which is exactly the drift the
# containerised run is meant to catch.
# `.dockerignore` keeps the Dockerfile in the context for this reason.
COPY .streamlit/ ./.streamlit/
COPY Dockerfile compose.yaml ./
RUN python -m pytest -q


# --- Runtime ---------------------------------------------------------------
FROM base AS runtime

# Copy application modules explicitly, including the shared project format.
COPY inlayer.py inlayer_project.py app.py app_helpers.py i18n.py ./
# The custom CSS in app.py expects this dark theme. Omitting the configuration
# falls back to the light theme and makes sidebar labels unreadable.
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
