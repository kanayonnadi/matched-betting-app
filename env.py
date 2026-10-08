"""Minimal ``.env`` loader (no external dependency).

Values already present in the environment win, so secrets can also be exported
normally. This just makes ``streamlit run app.py`` pick up the local ``.env``.
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env(path=ENV_PATH) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
