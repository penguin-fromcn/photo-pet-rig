"""PyInstaller entry point for the optional desktop runtime."""
from __future__ import annotations

from photopet.pet.app import main

if __name__ == "__main__":
    raise SystemExit(main())
