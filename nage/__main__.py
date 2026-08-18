#!/usr/bin/env python3
import os
import sys


if __package__ in (None, ""):
    # Keep direct source execution working while giving PyInstaller an
    # absolute package import instead of a relative __main__ import.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nage.cli import cli
else:
    from .cli import cli


if __name__ == "__main__":
    cli()
