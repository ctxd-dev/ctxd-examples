"""Bounded document discovery, search and reads through CTXD."""
from __future__ import annotations

import re
from ctxd.ctxfs_client import CtxfsClient
from context import PREFIX, check_read


class CtxdContext:
    def __init__(self, endpoint: str, token: str):
        self.client = CtxfsClient(endpoint=endpoint, api_key=token, timeout=10)

    def list_sources(self) -> dict:
        return self.client.glob("**/*.md", prefix=PREFIX.rstrip("/"), limit=100).model_dump()

    def search_sources(self, query: str) -> dict:
        if not query.strip() or len(query) > 200:
            raise ValueError("Search requires a phrase of 1–200 characters")
        # Treat the query as a literal phrase, with case-insensitive matching.
        return self.client.grep("(?i)" + re.escape(query), prefix=PREFIX.rstrip("/"), limit=20).model_dump()

    def read_source(self, path: str, start_line: int = 1, end_line: int = 80) -> dict:
        check_read(path, start_line, end_line)
        result = self.client.read_lines(path, start_line=start_line, end_line=end_line).model_dump()
        result["source_id"] = path.removeprefix(PREFIX)
        return result
