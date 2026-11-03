from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from context import digest, inventory
from ctxd_context import CtxdContext
from producer import initialize_credentials, publish


@pytest.fixture
def deployment(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/product", source)
    state = tmp_path / "state"
    initialize_credentials(state)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "ctxd.local_ctxfs_service", "serve", "--root", str(state / "store"),
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(endpoint + "/health", timeout=0.1).status_code == 401:
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.03)
        else:
            pytest.fail("CTXD server did not start")
        reader = (state / "reader.key").read_text()
        writer = (state / "writer.key").read_text()
        publish(source, endpoint, writer, state / "manifest.json")
        yield {"source": source, "state": state, "endpoint": endpoint,
               "reader": reader, "writer": writer, "process": process,
               "ctxd": CtxdContext(endpoint, reader)}
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def republish(d):
    return publish(d["source"], d["endpoint"], d["writer"], d["state"] / "manifest.json")


def test_ctxd_preserves_source_identity_and_evidence(deployment):
    context = deployment["ctxd"]
    documents = inventory(deployment["source"])
    assert set(documents) == {entry["path"] for entry in context.list_sources()["items"]}
    for path, text in documents.items():
        read = context.read_source(path)
        assert read["path"] == path and read["source_id"] == path.removeprefix("ctxd/")
        assert read["lines"] == text.splitlines()
        assert read["content_hash"] == digest(text)
    for phrase in ("Standard", "Enterprise", "Retry-After"):
        assert {x["path"] for x in context.search_sources(phrase)["items"]} == {
            path for path, text in documents.items() if phrase.casefold() in text.casefold()}


def test_updates_and_removals(deployment):
    d = deployment
    original = d["ctxd"].read_source("ctxd/pricing.md")
    pricing = d["source"] / "pricing.md"
    pricing.write_text(pricing.read_text().replace("300", "120"))
    (d["source"] / "sales.md").unlink()
    republish(d)
    after = d["ctxd"].read_source("ctxd/pricing.md")
    assert "120 requests" in "\n".join(after["lines"])
    assert after["content_hash"] != original["content_hash"]
    assert "ctxd/sales.md" not in {x["path"] for x in d["ctxd"].list_sources()["items"]}
    with pytest.raises(Exception):
        d["ctxd"].read_source("ctxd/sales.md")


def test_replay_does_not_duplicate_documents(deployment):
    before = deployment["ctxd"].list_sources()
    republish(deployment)
    initialize_credentials(deployment["state"])
    assert deployment["ctxd"].list_sources() == before


def test_offline_source_and_failed_inventory_preserve_store(deployment):
    d = deployment
    d["source"].rename(d["source"].with_name("offline-source"))
    with pytest.raises(ValueError, match="unavailable"):
        republish(d)
    assert len(d["ctxd"].list_sources()["items"]) == 5
    assert d["ctxd"].read_source("ctxd/api.md")["lines"]


def test_unsupported_file_prevents_partial_publication(deployment):
    d = deployment
    (d["source"] / "required.pdf").write_bytes(b"not text")
    (d["source"] / "sales.md").unlink()
    with pytest.raises(ValueError, match="Markdown"):
        republish(d)
    assert d["ctxd"].read_source("ctxd/sales.md")["lines"]


def test_empty_missing_partial_and_failed_reads_are_distinct(deployment):
    d = deployment
    provider = d["ctxd"]
    empty = provider.search_sources("unobtainium")
    assert empty["items"] == [] and empty["complete"]
    with pytest.raises(Exception):
        provider.read_source("ctxd/missing.md")
    with pytest.raises(ValueError):
        provider.read_source("../secret", 1, 3)
    (d["source"] / "long.md").write_text("Standard fact\n" * 30)
    republish(d)
    partial = d["ctxd"].search_sources("Standard")
    assert not partial["complete"] and partial["stopped_by"]
    d["process"].terminate()
    d["process"].wait(timeout=5)
    with pytest.raises(Exception):
        d["ctxd"].list_sources()


def test_readers_and_writers_have_different_capabilities(deployment):
    d = deployment
    url = d["endpoint"]
    assert httpx.get(url + "/api/ctxfs/ls").status_code == 401
    assert httpx.get(url + "/api/ctxfs/ls", headers={"Authorization": "Bearer " + d["writer"]}).status_code == 403
    assert httpx.post(url + "/api/ctxfs/submissions", headers={"Authorization": "Bearer " + d["reader"]}, json={}).status_code == 403
    with pytest.raises(Exception):
        CtxdContext(url, "invalid").list_sources()


def test_web_interface_uses_shared_key_and_streams_context(deployment):
    d = deployment
    snapshot = {"revision": "a" * 40, "files": {p: digest(t) for p, t in inventory(d["source"]).items()}}
    async def generator(payload, *, api_key, model):
        assert api_key == "operator-test-key"
        return {"answer": "Standard is 120. [ctxd/api.md:L3-L3]", "finished": True, "tokens": 100}
    client = TestClient(create_app(d["state"], d["endpoint"], d["state"] / "reader.key",
                                  api_key="operator-test-key", generator=generator,
                                  context_factory=lambda: d["ctxd"],
                                  publication_provider=lambda: {"current": snapshot}))
    assert client.get("/health").status_code == 200
    response = client.post("/api/ask", json={"question": "Standard rate limits"})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert any(e.get("name") == "read_source" for e in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "complete"
    assert client.post("/api/ask", json={"question": "setup", "backend": "sqlite"}).status_code == 422
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/ask", headers={"Origin": "https://other.example"},
                       json={"question": "rate limits"}).status_code == 403
