from __future__ import annotations

import argparse

from .prototype import run_prototype_tournament


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bcec")
    subparsers = parser.add_subparsers(dest="role", required=True)

    subparsers.add_parser("web", help="start the web server")
    subparsers.add_parser("runner", help="start the tournament runner")
    subparsers.add_parser("worker", help="start a worker client")

    args = parser.parse_args(argv)

    if args.role == "runner":
        run_prototype_tournament()
        return 0

    if args.role == "web":
        print("bcec web is not implemented yet")
        return 0

    if args.role == "worker":
        print("bcec worker is not implemented yet")
        return 0

    parser.error(f"unknown role: {args.role}")
    return 2

