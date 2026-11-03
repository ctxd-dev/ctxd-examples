"""Explicit document publication to the existing authenticated CTXD service."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx
from context import PREFIX, digest, inventory
from ctxfs.credentials import CredentialStore


def initialize_credentials(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with CredentialStore(state / "store") as store:
        for name, scopes, prefixes in (("reader", ["read", "status"], []),
                                        ("writer", ["submit"], [PREFIX.rstrip("/")])):
            path = state / f"{name}.key"
            if path.exists():
                if store.verify(path.read_text().strip()) is None:
                    raise ValueError(f"Existing {name} credential is invalid")
                continue
            token = store.create(name=f"example-{name}", scopes=scopes, prefixes=prefixes).token
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
                file.write(token)


def publish(source: Path, endpoint: str, token: str, manifest: Path) -> dict:
    documents = inventory(source)
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    current = {path: digest(text) for path, text in documents.items()}
    operations = [
        {"kind": "put", "path": path, "source_key": path,
         "source_version": current[path], "doc_class": "editable", "file_format": "markdown",
         "text": text, "provenance": {"producer": "ctxd-examples", "producer_version": "1"}}
        for path, text in documents.items()
    ]
    operations += [{"kind": "delete", "source_key": path, "source_version": str(uuid.uuid4())}
                   for path in previous.keys() - current.keys()]
    if operations:
        response = httpx.post(endpoint.rstrip("/") + "/api/ctxfs/submissions",
                              headers={"Authorization": f"Bearer {token}"}, timeout=15,
                              json={"writer_id": "docs-producer", "writer_prefix": PREFIX.rstrip("/"),
                                    "submission": {"run_id": str(uuid.uuid4()), "sequence": 0,
                                                   "is_full_scan": True, "operations": operations}})
        response.raise_for_status()
        if response.json()["state"] != "applied":
            raise RuntimeError("CTXD did not apply the submission; source manifest is unchanged")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_suffix(".pending")
    temporary.write_text(json.dumps(current, indent=2) + "\n")
    temporary.replace(manifest)
    return {"documents": len(documents), "operations": len(operations), "complete": True}
