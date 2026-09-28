#!/usr/bin/env python3
"""Zero-install entry script: `python mark-cli.py sync --files ...`.

No pip needed — just run it with the `mark/` package alongside it.
(It is named `mark-cli.py` because both `mark.py` and `mark` are taken:
a `mark.py` file would shadow the `mark/` package in Python's import
system, and `mark` is the package directory itself.)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mark.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
