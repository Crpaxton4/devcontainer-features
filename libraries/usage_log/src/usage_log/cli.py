"""``usage-log`` command line for hook scripts. Always exits 0 on a log failure."""

from __future__ import annotations

import json
import os
import sys

import usage_log

USAGE = (
    "usage: usage-log record <kind> <name> [key=value ...]\n"
    "       usage-log declare [FILE]   (JSON list of {kind, name, context}; stdin if no FILE)"
)


def _parse_context(pairs: list[str]) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in pairs if "=" in pair)


def _declare(args: list[str]) -> int:
    try:
        stream = open(args[0], encoding="utf-8") if args else sys.stdin
        with stream:
            items = json.load(stream)
    except (OSError, ValueError) as exc:
        print(f"usage-log: dropped declarations: {exc}", file=sys.stderr)
        return 0
    usage_log.declare(items)
    return 0


def main(argv: list[str] | None = None) -> int:
    if not os.environ.get(usage_log.ENV_DIR, "").strip():
        return 0
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["record"] and len(args) >= 3:
        usage_log.record(args[1], args[2], **_parse_context(args[3:]))
        return 0
    if args[:1] == ["declare"]:
        return _declare(args[1:])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
