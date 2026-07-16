#!/usr/bin/env python3
"""Compatibility entrypoint for the extension-owned podcast queue."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from processors.podcast import main  # noqa: E402


if __name__ == "__main__":
    main()
