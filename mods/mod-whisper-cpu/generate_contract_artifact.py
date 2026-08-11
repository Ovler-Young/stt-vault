"""Create the isolated Mod's contract artifact from the core source of truth."""

import sys
from pathlib import Path


def main() -> None:
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    main()
