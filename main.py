"""
Root entry point.

Kept as a one-line shim so the project can be run exactly as requested:

    python main.py

All real logic lives in `src/ai_swe/cli/main.py` -- this file just makes sure
`src/` is on the path (when not installed as a package) and delegates to it.
"""

import sys
from pathlib import Path

# Allow running directly from a source checkout without `pip install -e .`.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ai_swe.cli.main import app

if __name__ == "__main__":
    app()
