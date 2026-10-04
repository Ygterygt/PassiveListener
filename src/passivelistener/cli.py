"""Non-capturing offline artifact validator; never downloads model files."""

import argparse
import sys
from pathlib import Path

from passivelistener import __version__
from passivelistener.integrity import IntegrityError, verify_file


def main() -> int:
    parser = argparse.ArgumentParser(description="PassiveListener integration foundation")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify-artifact", help="check local bytes against an approved pin"
    )
    verify.add_argument("path", type=Path)
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--size", required=True, type=int)
    args = parser.parse_args()
    try:
        verify_file(args.path, args.sha256, args.size)
    except (IntegrityError, OSError):
        # Neither paths nor artifact content are included in operational output.
        print("artifact verification failed", file=sys.stderr)
        return 2
    print("artifact verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
