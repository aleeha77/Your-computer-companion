"""Entry point for ``python -m peeko``."""

import sys

from peeko.app import main

if __name__ == "__main__":
    sys.exit(main())