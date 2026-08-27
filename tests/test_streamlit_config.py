"""Checks the Streamlit runtime configuration and deprecated Streamlit APIs.

`.streamlit/config.toml` is not incidental: the Dockerfile copies it into the
runtime stage explicitly, and it carries both the pinned dark theme and the
upload limit. Neither can be set from application code — Streamlit reads server
options before the script starts — so they are pinned down here instead.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _REPO_ROOT / ".streamlit" / "config.toml"

# Default upload limit in MB. Overridable through the environment variable
# STREAMLIT_SERVER_MAX_UPLOAD_SIZE or the --server.maxUploadSize startup flag.
EXPECTED_MAX_UPLOAD_SIZE_MB = 250


def _config() -> dict:
    with _CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def test_config_file_exists():
    assert _CONFIG.is_file(), f"{_CONFIG} is missing"


def test_max_upload_size_default():
    """The default is 250 MB rather than Streamlit's own 200 MB."""
    assert _config()["server"]["maxUploadSize"] == EXPECTED_MAX_UPLOAD_SIZE_MB


def test_theme_stays_dark():
    """The custom CSS in app.py assumes a dark theme."""
    assert _config()["theme"]["base"] == "dark"


def test_dockerfile_copies_streamlit_config():
    """Without that COPY line neither theme nor upload limit reach the container."""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .streamlit/" in dockerfile


def test_no_deprecated_use_container_width_api():
    """`use_container_width` is deprecated - use `width=` instead.

    Streamlit warns on every session start and drops the parameter in an
    upcoming release; a regression would otherwise only surface in the log.
    """
    sources = ["app.py", "app_helpers.py", "i18n.py", "inlayer.py"]
    hits = [
        name for name in sources
        if "use_container_width" in (_REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    assert not hits, f"deprecated use_container_width in: {hits}"
