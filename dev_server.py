"""Local dev-server launcher for the Browser pane preview.

Exists because this project has no packaging metadata (see pyproject.toml)
- capint has always been run via PYTHONPATH=src, and README's own
"uvicorn capint.api.main:app" command actually depends on that being set
externally. This script does the same thing programmatically so a plain
`python dev_server.py` works with no env var required.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("capint.api.main:app", host="127.0.0.1", port=8000, reload=False)
