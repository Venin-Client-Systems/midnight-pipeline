#!/usr/bin/env python3
"""Binds this project to Midnight's shared project backend."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.environ.get("MIDNIGHT_SHARED_ROOT", str(Path.home() / "Developer" / "midnight")))
from project_backend import load_backend

backend = load_backend(Path(__file__).resolve().parents[1], "exampleapp")
if __name__ == "__main__":
    raise SystemExit(backend.entrypoint())
