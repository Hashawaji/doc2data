"""Allow `python -m doc2data`."""

import sys

from .cli import main

sys.exit(main())
