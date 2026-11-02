#!/usr/bin/env python3
"""Read-only JSONL viewer; Python standard library only."""

import argparse
import json
import sys
from pathlib import Path


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+", help="JSONL files to view")
    parser.add_argument("--record", type=positive_int, help="show only this record number (1-based, per file)")
    parser.add_argument("--indent", type=positive_int, default=2, help="indentation spaces (default: 2)")
    args = parser.parse_args()
    failed = False
    for path in args.files:
        try:
            with path.open(encoding="utf-8") as source:
                count = 0
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    count += 1
                    if args.record is not None and count != args.record:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        print(f"{path}:{line_number}: invalid JSON: {exc.msg} (column {exc.colno})", file=sys.stderr)
                        failed = True
                        continue
                    identifier = ""
                    if isinstance(record, dict):
                        for key in ("event_id", "decision_id", "case_id", "task"):
                            if key in record:
                                identifier = f" | {key}: {json.dumps(record[key], ensure_ascii=False)}"
                                break
                    print(f"\n--- {path} | record {count} | line {line_number}{identifier} ---\n")
                    print(json.dumps(record, indent=args.indent, ensure_ascii=False))
                if args.record is not None and count < args.record:
                    print(f"{path}: record {args.record} not found ({count} records)", file=sys.stderr)
                    failed = True
        except (OSError, UnicodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # A pager or pipe consumer may close before all records are printed.
        sys.exit(0)
