"""Offline maintenance commands; no microphone or network side effects."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from passivelistener import __version__
from passivelistener.archive import archive_transcripts
from passivelistener.archive_driver import run_request
from passivelistener.configuration import initialize_configuration, load_configuration
from passivelistener.integrity import IntegrityError
from passivelistener.models import MODEL_PINS, verified_model
from passivelistener.provisioning import provision_output
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
    archive = commands.add_parser("archive", help="archive finalized transcripts older than 48h")
    archive.add_argument("root", type=Path)
    qa_archive = commands.add_parser("archive-test-request", help="local synthetic QA protocol")
    qa_archive.add_argument("request", type=Path)
    for command in ("init-config", "validate-config", "provision-output"):
        configuration = commands.add_parser(command, help="user-bound configuration maintenance")
        configuration.add_argument("directory", type=Path)
    commands.add_parser("worker-bootstrap-check", help="frozen worker admission diagnostic")
    session = commands.add_parser("session-diagnostic", help="foreground session lifecycle only")
    session.add_argument("--acknowledge-diagnostic", action="store_true")
    args = parser.parse_args()
    if args.command == "session-diagnostic":
        from passivelistener.session_entry import run_session_diagnostic

        return run_session_diagnostic(args.acknowledge_diagnostic)
    if args.command == "worker-bootstrap-check":
        from passivelistener.bootstrap import check_frozen_worker

        try:
            result_code = check_frozen_worker()
        except Exception:
            result_code = 70
        print(f"worker bootstrap result: {result_code}")
        return result_code
    if args.command in ("init-config", "validate-config", "provision-output"):
        try:
            if args.command == "init-config":
                initialize_configuration(args.directory)
            else:
                settings = load_configuration(args.directory)
                if args.command == "provision-output":
                    provision_output(Path(settings.output_directory))
        except (OSError, ValueError):
            print("configuration operation failed", file=sys.stderr)
            return 2
        print("configuration operation completed")
        return 0
    if args.command in ("archive", "archive-test-request"):
        try:
            result = (run_request(args.request) if args.command == "archive-test-request"
                      else asdict(archive_transcripts(args.root)))
        except (OSError, ValueError):
            print("archive operation failed", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0 if args.command == "archive-test-request" or result["deferred"] == 0 else 1
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

