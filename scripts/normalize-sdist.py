#!/usr/bin/env python3
"""Normalize an sdist tarball for reproducible builds.

Setuptools (including v82) does not fully honour SOURCE_DATE_EPOCH when
building an sdist:

  - The gzip wrapper's mtime header field records the moment gzip was
    opened (time.time()), not SOURCE_DATE_EPOCH.
  - The gzip FNAME field encodes the temporary file-name used during
    the build, which contains a path-prefix that can vary run to run.
  - Directory entries and generated files (PKG-INFO, setup.cfg, the
    egg-info directory) are created during the build, so their mtime
    is the current clock time, not SOURCE_DATE_EPOCH.
  - Python's tarfile module defaults to PAX format, which stores
    mtime with sub-second precision in extended headers; this
    fractional component varies between builds even when the integer
    part is the same.

This script rewrites an existing sdist tarball to eliminate all of
these sources of variance:

  1. Reads the existing .tar.gz.
  2. Re-writes every tar member with:
       - mtime set to SOURCE_DATE_EPOCH (integer, no fraction)
       - pax_headers cleared (removes the fractional-mtime PAX field)
       - uid/gid/uname/gname zeroed (removes build-host user metadata)
  3. Sorts member order alphabetically for deterministic ordering.
  4. Re-compresses with gzip using:
       - mtime = SOURCE_DATE_EPOCH (fixes the gzip wrapper mtime field)
       - filename = '' (suppresses the gzip FNAME header field)

Usage (CI):
    python scripts/normalize-sdist.py <SOURCE_DATE_EPOCH> <sdist.tar.gz>

The file is rewritten in place.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import sys
import tarfile


def normalize(sdist_path: str, epoch: int) -> None:
    """Rewrite *sdist_path* in-place with deterministic metadata."""
    # --- Pass 1: read the original tarball into memory ------------------
    tar_buf = io.BytesIO()
    with (
        tarfile.open(sdist_path, "r:gz") as src,
        tarfile.open(fileobj=tar_buf, mode="w:", format=tarfile.PAX_FORMAT) as dst,
    ):
        for member in sorted(src.getmembers(), key=lambda m: m.name):
            # Normalize timestamp to integer SOURCE_DATE_EPOCH.
            member.mtime = epoch
            # Clear PAX extended headers — they carry the original
            # high-precision (fractional-second) mtime which varies
            # between builds.
            member.pax_headers = {}
            # Normalize ownership — build-host user IDs are not
            # reproducible across machines.
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            if member.isreg():
                fileobj = src.extractfile(member)
                dst.addfile(member, fileobj)
            else:
                dst.addfile(member)

    raw_tar = tar_buf.getvalue()

    # --- Pass 2: re-compress with a deterministic gzip header -----------
    gz_buf = io.BytesIO()
    with gzip.GzipFile(
        filename="",  # suppress FNAME header field (it would encode a
        # non-deterministic temporary path)
        mode="wb",
        fileobj=gz_buf,
        mtime=epoch,  # fix the gzip mtime field to SOURCE_DATE_EPOCH
    ) as gz:
        gz.write(raw_tar)

    result = gz_buf.getvalue()

    # --- Write back in-place -------------------------------------------
    with open(sdist_path, "wb") as f:
        f.write(result)

    sha256 = hashlib.sha256(result).hexdigest()
    print(f"normalized {sdist_path}")
    print(f"  SOURCE_DATE_EPOCH = {epoch}")
    print(f"  sha256 = {sha256}")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {sys.argv[0]} <SOURCE_DATE_EPOCH> <sdist.tar.gz>",
            file=sys.stderr,
        )
        return 2

    try:
        epoch = int(sys.argv[1])
    except ValueError:
        print(f"error: SOURCE_DATE_EPOCH must be an integer, got: {sys.argv[1]!r}", file=sys.stderr)
        return 2

    sdist_path = sys.argv[2]
    normalize(sdist_path, epoch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
