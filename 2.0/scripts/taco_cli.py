#!/usr/bin/env python3
"""Entry point: ./trade.sh taco <subcommand> ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.taco.cli import main  # noqa: E402

sys.exit(main())
