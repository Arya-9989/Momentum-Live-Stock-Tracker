"""Streamlit entrypoint: ``streamlit run streamlit_app.py``.

All logic lives in the ``momentum`` package under ``src/``. When the package
is installed (``uv sync``, Docker) it is imported normally; on hosts that only
install dependencies, such as Streamlit Community Cloud with
``requirements.txt``, ``src/`` is added to the import path instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from momentum.app import main
except ModuleNotFoundError:  # pragma: no cover - exercised on Streamlit Cloud
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from momentum.app import main

main()
