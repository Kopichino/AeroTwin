#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to a file for review and client generation.

Committing the schema makes API changes visible as reviewable diffs in pull
requests, and lets CI fail the build when the generated TypeScript client would
drift from the server (Doc 12 section 12.10, NFR-12).

Usage:
    python tools/codegen/export_openapi.py --output docs/api/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_importable() -> None:
    """Allow running from a source checkout without an editable install."""
    for candidate in (
        REPO_ROOT / "services" / "api" / "src",
        REPO_ROOT / "libs" / "at_core" / "src",
        REPO_ROOT / "libs" / "at_config" / "src",
        REPO_ROOT / "libs" / "at_observability" / "src",
    ):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def build_schema() -> dict[str, Any]:
    """Instantiate the app with deterministic settings and return its schema.

    A fixed profile is used so the emitted document never varies with the
    developer's local environment -- otherwise the drift check would be flaky.
    """
    _ensure_importable()

    # Imported lazily so `--help` works without the app dependencies installed.
    from at_api.main import create_app
    from at_config import Profile, Settings

    settings = Settings(profile=Profile.CI, service_name="api", log_level="ERROR")
    app = create_app(settings)
    schema: dict[str, Any] = app.openapi()
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / "api" / "openapi.json",
        help="Destination path for the schema document.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk differs from the generated schema.",
    )
    args = parser.parse_args()

    schema = build_schema()
    # sort_keys keeps the diff stable across Python versions and dict ordering.
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.output.exists():
            print(f"error: {args.output} does not exist", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"error: {args.output} is out of date. Run 'make openapi'.",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is current.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")

    paths = len(schema.get("paths", {}))
    schemas = len(schema.get("components", {}).get("schemas", {}))
    print(f"Wrote {args.output.relative_to(REPO_ROOT)}: {paths} paths, {schemas} schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
