"""NASA C-MAPSS dataset acquisition (option (a): automatic download).

Downloads the official PHM repository archive, verifies its checksum, unpacks the
nested zip, and validates the extracted files against the expected trajectory
counts before declaring success. A silently truncated download that produces a
plausible-looking but wrong dataset is far more damaging than a loud failure, so
every stage is verified.

Usage:
    python -m at_data.acquire --dest data/raw/cmapss
    make data
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Official NASA Prognostics Center of Excellence data repository mirror.
CMAPSS_URL = (
    "https://phm-datasets.s3.amazonaws.com/NASA/"
    "6.+Turbofan+Engine+Degradation+Simulation+Data+Set.zip"
)

#: SHA-256 of the outer archive, verified 2026-07-26. A mismatch means the upstream
#: artefact changed and the expectations below must be re-validated before trusting it.
CMAPSS_SHA256 = "c9c5dec12a945a82e8bb4446589d7fb3cc057b5e5d81fa1a12e25ee9912ad3b2"

EXPECTED_ARCHIVE_BYTES = 12_429_152

SUBSETS = ("FD001", "FD002", "FD003", "FD004")

#: Column count of every telemetry row: unit, cycle, 3 op settings, 21 sensors.
EXPECTED_COLUMNS = 26


@dataclass(frozen=True, slots=True)
class SubsetExpectation:
    """Ground-truth shape of one subset, measured from the official release."""

    subset: str
    train_rows: int
    test_rows: int
    train_units: int
    test_units: int
    test_min_length: int


#: Verified by direct measurement in M2. These are assertions, not documentation:
#: ingestion fails if the downloaded data does not match.
EXPECTATIONS: dict[str, SubsetExpectation] = {
    "FD001": SubsetExpectation("FD001", 20_631, 13_096, 100, 100, 31),
    "FD002": SubsetExpectation("FD002", 53_759, 33_991, 260, 259, 21),
    "FD003": SubsetExpectation("FD003", 24_720, 16_596, 100, 100, 38),
    "FD004": SubsetExpectation("FD004", 61_249, 41_214, 249, 248, 19),
}

REQUIRED_FILES = tuple(
    f"{kind}_{subset}.txt" for subset in SUBSETS for kind in ("train", "test", "RUL")
)


class AcquisitionError(RuntimeError):
    """Raised when the dataset cannot be obtained or fails verification."""


def sha256_of(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Stream a file through SHA-256 without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, *, verbose: bool = True) -> Path:
    """Fetch ``url`` to ``destination`` with a simple progress indicator."""
    if verbose:
        print(f"Downloading {url}")

    # Only animate when attached to a terminal; in CI or a pipe this would
    # otherwise emit thousands of useless progress lines into the log.
    interactive = verbose and sys.stdout.isatty()
    last_pct = -5.0

    def _hook(block_num: int, block_size: int, total_size: int) -> None:
        nonlocal last_pct
        if not verbose or total_size <= 0:
            return
        downloaded = min(block_num * block_size, total_size)
        pct = 100.0 * downloaded / total_size
        if interactive:
            print(f"\r  {downloaded / 1e6:6.1f} / {total_size / 1e6:.1f} MB ({pct:5.1f}%)", end="")
        elif pct - last_pct >= 25.0:
            last_pct = pct
            print(f"  {downloaded / 1e6:.1f} / {total_size / 1e6:.1f} MB ({pct:.0f}%)")

    try:
        # URL is a module constant pointing at the official NASA mirror, and the
        # archive checksum is verified by the caller before anything is unpacked.
        urllib.request.urlretrieve(url, destination, reporthook=_hook)
    except OSError as exc:
        raise AcquisitionError(f"download failed: {exc}") from exc
    if interactive:
        print()
    return destination


def extract_nested(archive: Path, target: Path, *, verbose: bool = True) -> None:
    """Unpack the archive, following the one level of nesting NASA ships.

    The published artefact is a zip containing ``CMAPSSData.zip``; the text files
    live inside the inner archive.
    """
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        with zipfile.ZipFile(archive) as outer:
            outer.extractall(staging)

        inner_archives = sorted(staging.rglob("*.zip"))
        if inner_archives:
            if verbose:
                print(f"  unpacking nested archive: {inner_archives[0].name}")
            with zipfile.ZipFile(inner_archives[0]) as inner:
                inner.extractall(staging)

        copied = 0
        for path in staging.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".txt", ".pdf"}:
                shutil.copy2(path, target / path.name)
                copied += 1

        if verbose:
            print(f"  extracted {copied} files to {target}")


def verify(directory: Path, *, verbose: bool = True) -> dict[str, SubsetExpectation]:
    """Validate the extracted dataset against the expected shape.

    Checks presence, column count, row count, unit count and the shortest test
    trajectory. Raises ``AcquisitionError`` describing the first discrepancy.
    """
    missing = [name for name in REQUIRED_FILES if not (directory / name).is_file()]
    if missing:
        raise AcquisitionError(f"missing expected files: {', '.join(missing)}")

    for subset, expectation in EXPECTATIONS.items():
        for split in ("train", "test"):
            path = directory / f"{split}_{subset}.txt"
            units: dict[int, int] = {}
            rows = 0
            with path.open() as handle:
                for lineno, line in enumerate(handle, start=1):
                    parts = line.split()
                    if not parts:
                        continue
                    if len(parts) != EXPECTED_COLUMNS:
                        raise AcquisitionError(
                            f"{path.name}:{lineno} has {len(parts)} columns, "
                            f"expected {EXPECTED_COLUMNS}"
                        )
                    rows += 1
                    unit = int(parts[0])
                    units[unit] = units.get(unit, 0) + 1

            want_rows = expectation.train_rows if split == "train" else expectation.test_rows
            want_units = expectation.train_units if split == "train" else expectation.test_units

            if rows != want_rows:
                raise AcquisitionError(f"{path.name}: {rows:,} rows, expected {want_rows:,}")
            if len(units) != want_units:
                raise AcquisitionError(f"{path.name}: {len(units)} units, expected {want_units}")
            if split == "test" and min(units.values()) != expectation.test_min_length:
                raise AcquisitionError(
                    f"{path.name}: shortest trajectory {min(units.values())}, "
                    f"expected {expectation.test_min_length}"
                )

        rul_path = directory / f"RUL_{subset}.txt"
        rul_count = sum(1 for line in rul_path.read_text().splitlines() if line.strip())
        if rul_count != expectation.test_units:
            raise AcquisitionError(
                f"{rul_path.name}: {rul_count} labels, expected {expectation.test_units}"
            )

        if verbose:
            print(
                f"  {subset}: {expectation.train_rows:>7,} train / "
                f"{expectation.test_rows:>7,} test rows  "
                f"({expectation.train_units} / {expectation.test_units} units)  OK"
            )

    return EXPECTATIONS


def acquire(
    dest: Path,
    *,
    force: bool = False,
    verify_checksum: bool = True,
    verbose: bool = True,
) -> Path:
    """Ensure the C-MAPSS dataset is present and valid at ``dest``.

    Idempotent: an already-valid directory short-circuits without downloading.
    """
    dest = dest.expanduser().resolve()

    if not force and all((dest / name).is_file() for name in REQUIRED_FILES):
        try:
            verify(dest, verbose=False)
        except AcquisitionError:
            if verbose:
                print("Existing dataset failed verification; re-downloading.")
        else:
            if verbose:
                print(f"Dataset already present and valid at {dest}")
            return dest

    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "cmapss.zip"
        download(CMAPSS_URL, archive, verbose=verbose)

        size = archive.stat().st_size
        if size != EXPECTED_ARCHIVE_BYTES and verbose:
            print(f"  warning: archive is {size:,} bytes, expected {EXPECTED_ARCHIVE_BYTES:,}")

        if verify_checksum:
            actual = sha256_of(archive)
            if actual != CMAPSS_SHA256:
                raise AcquisitionError(
                    "checksum mismatch -- refusing to use this archive.\n"
                    f"  expected {CMAPSS_SHA256}\n"
                    f"  actual   {actual}\n"
                    "The upstream artefact may have changed; re-validate before "
                    "updating CMAPSS_SHA256."
                )
            if verbose:
                print(f"  checksum OK ({actual[:16]}...)")

        extract_nested(archive, dest, verbose=verbose)

    if verbose:
        print("Verifying extracted dataset:")
    verify(dest, verbose=verbose)

    if verbose:
        total = sum(e.train_rows + e.test_rows for e in EXPECTATIONS.values())
        print(f"Dataset ready at {dest} ({total:,} telemetry rows).")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path("data/raw/cmapss"))
    parser.add_argument("--force", action="store_true", help="Re-download even if present")
    parser.add_argument("--no-checksum", action="store_true", help="Skip checksum verification")
    parser.add_argument("--verify-only", action="store_true", help="Validate without downloading")
    args = parser.parse_args(argv)

    try:
        if args.verify_only:
            verify(args.dest.expanduser().resolve())
            print("Dataset verification passed.")
        else:
            acquire(args.dest, force=args.force, verify_checksum=not args.no_checksum)
    except AcquisitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
