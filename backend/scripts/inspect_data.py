#!/usr/bin/env python3
"""Print a bounded, read-only structural inspection of the demo data package."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from pprint import pformat
from typing import Any


SAMPLE_SIZE = 2


def relative_files(directory: Path, root: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return [
        path.relative_to(root).as_posix()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]


def json_structure(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "list[empty]"
        first = value[0]
        if isinstance(first, dict):
            return f"list[{len(value)}] of objects with keys {sorted(first)}"
        return f"list[{len(value)}] of {type(first).__name__}"
    if isinstance(value, dict):
        return f"object with keys {sorted(value)}"
    return type(value).__name__


def json_sample(value: Any) -> Any:
    if isinstance(value, list):
        return value[:SAMPLE_SIZE]
    return value


def inspect_json(path: Path, root: Path) -> None:
    print(f"\nJSON: {path.relative_to(root).as_posix()}")
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"  ERROR: {error}")
        return

    print(f"  top-level: {json_structure(value)}")
    print(f"  sample: {pformat(json_sample(value), sort_dicts=True)}")


def read_csv_sample(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = []
        for index, row in enumerate(reader):
            if index >= SAMPLE_SIZE:
                break
            rows.append(dict(row))
    return columns, rows


def inspect_csv(path: Path, root: Path, label: str = "CSV") -> None:
    print(f"\n{label}: {path.relative_to(root).as_posix()}")
    try:
        columns, rows = read_csv_sample(path)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        print(f"  ERROR: {error}")
        return

    print(f"  columns: {columns}")
    print(f"  sample rows: {pformat(rows, sort_dicts=False)}")


def inspect_company(company_dir: Path, root: Path) -> None:
    print(f"\n{'=' * 72}")
    print(f"Company folder: {company_dir.name}")

    api_dir = company_dir / "api"
    print("API files:")
    api_files = relative_files(api_dir, root)
    for filename in api_files:
        print(f"  - {filename}")
    if not api_files:
        print("  (none)")

    financial_dir = company_dir / "financial"
    print("Financial files:")
    financial_files = relative_files(financial_dir, root)
    for filename in financial_files:
        print(f"  - {filename}")
    if not financial_files:
        print("  (none)")

    users_path = company_dir / "users.csv"
    if users_path.is_file():
        inspect_csv(users_path, root, label="users.csv")
    else:
        print("\nusers.csv: (missing)")

    company_path = company_dir / "company.json"
    if company_path.is_file():
        try:
            with company_path.open(encoding="utf-8") as handle:
                company_value = json.load(handle)
            keys = sorted(company_value) if isinstance(company_value, dict) else []
            print(f"\ncompany.json keys: {keys}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"\ncompany.json keys: ERROR: {error}")
    else:
        print("\ncompany.json keys: (missing)")

    for json_path in sorted(company_dir.rglob("*.json")):
        inspect_json(json_path, root)

    for csv_path in sorted(company_dir.rglob("*.csv")):
        if csv_path != users_path:
            inspect_csv(csv_path, root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect tenant folders, JSON structures, and CSV samples."
    )
    parser.add_argument(
        "data_dir",
        nargs="?",
        default="data",
        type=Path,
        help="Data root to inspect (default: data)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.data_dir.expanduser().resolve()
    if not root.is_dir():
        print(f"Data directory does not exist: {root}")
        return 1

    company_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    print(f"Data root: {root}")
    print(f"Company folders: {[path.name for path in company_dirs]}")

    for company_dir in company_dirs:
        inspect_company(company_dir, root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
