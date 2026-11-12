"""Static files are kept by browsers for a year, so the address of each must change whenever any of them does."""

import hashlib
from pathlib import Path


def content_hash(directory: Path) -> str:
    """A short SHA-256 of every file under `directory`: its relative path and its bytes, in sorted order. Each part is
    prefixed with its length, so no two different sets of files run together into the same input."""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        name = path.relative_to(directory).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()[:16]
