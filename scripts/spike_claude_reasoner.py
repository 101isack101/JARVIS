"""Spike historico del reasoner. Ahora ejecuta Grok 4.6."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "grok" / "reasoner.py"), run_name="__main__")
