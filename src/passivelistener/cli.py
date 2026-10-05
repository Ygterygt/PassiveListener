"""Non-capturing offline artifact validator; never downloads model files."""

import argparse
import sys
from pathlib import Path

from passivelistener import __version__
from passivelistener.integrity import IntegrityError
from passivelistener.models import MODEL_PINS, verified_model
from passivelistener.windows_input import verified_input


def main() -> int:
    parser = argparse.ArgumentParser(description="PassiveListener integration foundation")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify-artifact", help="diagnostic: check bytes against a caller-supplied hash"
    )
    verify.add_argument("path", type=Path)
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--size", required=True, type=int)
    model = commands.add_parser("verify-model", help="check bytes against compiled model pins")
    model.add_argument("model_id", choices=tuple(MODEL_PINS))
    model.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        lease = (verified_model(args.model_id, args.directory.absolute())
                 if args.command == "verify-model"
                 else verified_input(args.path.absolute(), args.sha256, args.size))
        with lease:
            pass
    except (IntegrityError, OSError):
        # Neither paths nor artifact content are included in operational output.
        print("artifact verification failed", file=sys.stderr)
        return 2
    print("artifact verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
