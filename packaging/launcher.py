"""Entry script for the packaged app (PyInstaller); same as ``python -m action_macro``."""

import sys

from action_macro.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
