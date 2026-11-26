"""MuseDeck 主入口。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from musedeck.web_app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
