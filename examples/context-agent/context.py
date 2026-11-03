"""Canonical document paths, source inventory and bounded-read validation."""
from __future__ import annotations

import hashlib
from pathlib import Path

PREFIX = "ctxd/"


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def inventory(source: Path) -> dict[str, str]:
    if not source.is_dir():
        raise ValueError(f"Source directory is unavailable: {source}")
    documents = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Resolve the symlink before ingestion: {path.name}")
        if path.is_file():
            if path.suffix != ".md" or path.stat().st_size > 5_000_000:
                raise ValueError(f"This producer requires Markdown under 5 MB: {path.name}")
            documents[PREFIX + path.relative_to(source).as_posix()] = path.read_text(encoding="utf-8")
    if not documents:
        raise ValueError("Source inventory is empty; refusing to replace a corpus")
    return documents


def check_read(path: str, start_line: int, end_line: int) -> None:
    if not path.startswith(PREFIX) or ".." in Path(path).parts:
        raise ValueError("Read a canonical ctxd/ path returned by a context tool")
    if not 1 <= start_line <= end_line or end_line - start_line >= 80:
        raise ValueError("Read at most 80 lines, using positive line numbers")
