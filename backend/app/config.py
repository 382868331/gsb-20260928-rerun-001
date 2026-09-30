"""Runtime configuration.

Everything the editor writes (uploaded media, project files, exports)
lives under a single data root, so the repository directory never gets
modified by the running app and original media is never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
DATA_ROOT = Path(os.environ.get("EDITOR_DATA_ROOT", DEFAULT_DATA_ROOT)).resolve()
STAGING_DIR = DATA_ROOT / "staging"
MEDIA_DIR = DATA_ROOT / "media"
PROJECTS_DIR = DATA_ROOT / "projects"
EXPORTS_DIR = DATA_ROOT / "exports"
SAMPLES_DIR = DATA_ROOT / "samples"


def ensure_dirs() -> None:
    for path in (STAGING_DIR, MEDIA_DIR, PROJECTS_DIR, EXPORTS_DIR, SAMPLES_DIR):
        path.mkdir(parents=True, exist_ok=True)
