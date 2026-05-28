# Reproducible builds

This document describes what is guaranteed for hangarfit release artifacts,
what the remaining limitations are, and how a third party can independently
verify that an sdist downloaded from a GitHub Release was built from the
tagged source tree.

---

## What "reproducible" means here

A **reproducible build** is one where rebuilding the same source at the same
version produces a bit-for-bit identical artifact. The practical benefit: a
third party can download a release artifact, rebuild from the tagged commit,
and confirm the two SHA-256 digests match — proving that the release was
built from the declared source and nothing else.

---

## What is guaranteed: the sdist

Starting with v0.8.0, the sdist (`hangarfit-<version>.tar.gz`) is normalized
after `python -m build` produces it, before Sigstore signing. The
normalization script ([`scripts/normalize-sdist.py`](../scripts/normalize-sdist.py))
eliminates all sources of build-time variance that setuptools v82 does not
suppress on its own:

| Field | Variance source | Fix |
|---|---|---|
| Gzip wrapper `mtime` field | `time.time()` at build moment | Set to `SOURCE_DATE_EPOCH` |
| Gzip `FNAME` header field | Temporary file path | Suppressed (`filename=''`) |
| Tar member `mtime` (integer) | Current clock time for generated files | Set to `SOURCE_DATE_EPOCH` |
| Tar member `mtime` (fractional) | PAX extended header sub-second precision | `pax_headers` cleared |
| `uid`/`gid`/`uname`/`gname` | Build-host user account | Zeroed |
| Member order | Filesystem traversal order | Sorted alphabetically |

`SOURCE_DATE_EPOCH` is derived deterministically from the release commit:

```
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct <tag>)
```

This value is the committer timestamp of the tagged commit. Any verifier who
has a clone of the repository can derive the same integer from the same tag,
with no out-of-band communication.

### Caveat: the compressed `.tar.gz` digest also depends on zlib

`normalize-sdist.py` makes the **tar payload** (member order, names, contents,
and metadata) deterministic. But the released artifact is that tar *gzip-
compressed*, and the gzip payload is a DEFLATE stream produced by the
interpreter's linked `zlib`. Different `zlib` versions/implementations
(e.g. zlib 1.2.x vs 1.3, or the `zlib-ng` fork several distros now ship) can
emit a **different compressed byte stream from identical input**. So the
SHA-256 of the final `.tar.gz` is reproducible only for a verifier whose
`zlib` matches the release runner's (CPython 3.12 on `ubuntu-latest`).

To compare *independently of compression*, `gunzip` both artifacts and compare
the inner `.tar` SHA-256 — that digest is reproducible regardless of zlib:

```bash
gunzip -c released.tar.gz   | sha256sum
gunzip -c dist/rebuilt.tar.gz | sha256sum   # must match
```

You can record the release runner's zlib with
`python3 -c "import zlib; print(zlib.ZLIB_RUNTIME_VERSION)"`.

### Proof of reproducibility (local experiment)

The following proof was run during development (branch `feature/reproducible-sdist-244`):

```
$ SOURCE_DATE_EPOCH=1748304000 python3 -m build --sdist --no-isolation --outdir /tmp/run1
$ SOURCE_DATE_EPOCH=1748304000 python3 -m build --sdist --no-isolation --outdir /tmp/run2
$ sha256sum /tmp/run1/*.tar.gz /tmp/run2/*.tar.gz
51bc765a...  /tmp/run1/hangarfit-0.7.1.tar.gz    # differ — raw build is NOT reproducible
dce6245a...  /tmp/run2/hangarfit-0.7.1.tar.gz

# After normalize-sdist.py with epoch=1748304000:
$ python scripts/normalize-sdist.py 1748304000 /tmp/run1/hangarfit-0.7.1.tar.gz
$ python scripts/normalize-sdist.py 1748304000 /tmp/run2/hangarfit-0.7.1.tar.gz
$ sha256sum /tmp/run1/*.tar.gz /tmp/run2/*.tar.gz
646af2d6...  /tmp/run1/hangarfit-0.7.1.tar.gz    # match — reproducible
646af2d6...  /tmp/run2/hangarfit-0.7.1.tar.gz

# Different SOURCE_DATE_EPOCH → different hash (the knob works):
$ python scripts/normalize-sdist.py 1234567890 /tmp/run1/hangarfit-0.7.1.tar.gz
e90e98e3...  /tmp/run1/hangarfit-0.7.1.tar.gz    # different from above — epoch controls output
```

---

## What is NOT guaranteed: the wheel (STRETCH / future work)

The wheel (`hangarfit-<version>-py3-none-any.whl`) is a ZIP archive.
ZIP timestamps have 2-second resolution and setuptools/wheel already honours
`SOURCE_DATE_EPOCH` for zip entry timestamps. In practice the wheel is
**close to reproducible** across multiple builds on the same toolchain, but
the following are not yet normalized:

- The `RECORD` file inside the wheel lists SHA-256 digests and sizes of
  every wheel entry; if any entry's content varies, RECORD varies.
- The `WHEEL` metadata file contains `Generator: bdist_wheel ...` which
  encodes the wheel package version — stable for a pinned toolchain but
  not across toolchain upgrades.
- ZIP local-file header timestamps can differ by 2-second rounding
  depending on the exact sub-second clock reading.

Wheel reproducibility is left as future work. Do not rely on the wheel
SHA-256 matching a rebuilt wheel until a future release explicitly
documents it as guaranteed.

---

## Third-party verification steps

To verify that a published sdist matches a fresh build from the tagged source:

### Prerequisites

- Python 3.12
- The exact build toolchain: `pip install --require-hashes -r requirements-build.txt`
  (installs `build==1.5.0`, `setuptools==82.0.1`, `wheel==0.47.0` — the same
  versions used by the release workflow)
- To match the **compressed `.tar.gz`** digest, the same `zlib` as the release
  runner (CPython 3.12 on `ubuntu-latest`). If your `zlib` differs, compare the
  decompressed inner `.tar` instead (see the zlib caveat above) — that digest is
  zlib-independent.

### Steps

```bash
# 1. Clone and check out the release tag
git clone https://github.com/DocGerd/hangarfit.git
cd hangarfit
git checkout v<version>

# 2. Derive SOURCE_DATE_EPOCH from the tagged commit
#    (same formula the release workflow uses)
SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
echo "SOURCE_DATE_EPOCH = $SOURCE_DATE_EPOCH"

# 3. Install the pinned build toolchain
pip install --require-hashes -r requirements-build.txt

# 4. Build the sdist
python -m build --sdist --no-isolation

# 5. Normalize the sdist
python scripts/normalize-sdist.py "$SOURCE_DATE_EPOCH" dist/hangarfit-<version>.tar.gz

# 6. Compare SHA-256 against the released artifact
sha256sum dist/hangarfit-<version>.tar.gz
# Compare to the SHA-256 of the .tar.gz downloaded from the GitHub Release page.
```

The SHA-256 of the locally produced, normalized sdist must match the SHA-256
of the artifact on the GitHub Release page. If they differ, the released
artifact was not built from the declared source.

### What a mismatch means

A mismatch can indicate:
- The release artifact was tampered with after signing.
- The release was built from a different commit than the declared tag
  (e.g., the tag was force-pushed — detectable by comparing `git rev-parse <tag>`
  to the commit SHA logged by the release workflow run).
- The build toolchain was not pinned correctly (different `setuptools`/`wheel`
  versions can produce different `PKG-INFO` metadata or entry ordering).
- Your `zlib` differs from the release runner's, changing only the gzip
  compression of an otherwise-identical tar. Rule this out by comparing the
  decompressed inner `.tar` digests (see the zlib caveat above) before
  suspecting tampering.

The Sigstore signature (`.sigstore.json` bundle next to each artifact)
independently verifies that the artifact was produced by
`.github/workflows/release.yml` in this repository. A valid Sigstore
signature plus a matching SHA-256 rebuid gives high confidence in the
artifact's provenance.

---

## Why SOURCE_DATE_EPOCH alone is not enough

Setting `SOURCE_DATE_EPOCH` in the environment is sufficient for some build
systems (e.g. flit, hatch) that explicitly read the variable and apply it
to all archive metadata. Setuptools v82 does not do this fully for sdists:

- `tarfile.open(name, 'w|gz')` opens a live gzip stream whose `mtime`
  field is set to `time.time()` at the moment the file is opened — not
  `SOURCE_DATE_EPOCH`.
- Generated files and directories created during the build (e.g.
  `PKG-INFO`, the egg-info directory) receive the filesystem mtime of
  the moment they are written.
- Python's `tarfile` module defaults to PAX format, which encodes mtime
  with nanosecond precision in extended headers; even if the integer
  second matches, the sub-second fraction varies.

These gaps are documented upstream in
[pypa/setuptools#2133](https://github.com/pypa/setuptools/issues/2133).
The `scripts/normalize-sdist.py` post-processor closes all four gaps.
If a future setuptools version handles them natively, the normalization
step becomes a no-op (the output is already deterministic) and can be
removed.

---

## Relationship to other security documents

- **Sigstore signing** — every release artifact (sdist and wheel) is
  signed with keyless cosign. Verification instructions live in
  [`docs/security-posture.md`](security-posture.md) and in the release
  workflow ([`.github/workflows/release.yml`](../.github/workflows/release.yml)).
- **Supply-chain hardening** — the build toolchain is hash-pinned in
  `requirements-build.txt`; the rationale is in
  [`docs/security-posture.md`](security-posture.md).
- **OpenSSF Best Practices** — reproducible builds are a Gold-level
  criterion (`reproducible_build`). This document and the associated
  workflow change constitute the evidence for that criterion.
