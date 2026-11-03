"""Data-container service and GitHub document synchronization; no model credentials are stored here."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import certifi
import httpx
import uvicorn
from fastapi import HTTPException, Request
from ctxd.local_ctxfs_server import create_ctxfs_app, CtxfsAuthMode
from ctxfs.auth import Scope, authorize

from producer import initialize_credentials

STATE = Path(os.environ.get("DATA_STATE", "/data"))
CONNECTION = Path(os.environ.get("CONNECTION_DIR", "/connection"))
from docs_sync import sync_docs

SYNC_SECONDS = max(60, int(os.environ.get("SYNC_SECONDS", "300")))
ENDPOINT = "https://127.0.0.1:8850"


def create_data_app(state):
    app = create_ctxfs_app(state / "store", auth_mode=CtxfsAuthMode.BEARER)

    def require_read(request):
        if not authorize(request.state.ctxfs_principal, Scope.READ).allowed:
            raise HTTPException(403, "Read credential required")

    @app.get("/example/status")
    def status(request: Request):
        require_read(request)
        publication = state / "publication.json"
        if not publication.exists():
            raise HTTPException(503, "Documentation is not published yet")
        return json.loads(publication.read_text())

    return app


def initialize():
    initialize_credentials(STATE)
    CONNECTION.mkdir(parents=True, exist_ok=True)
    if not (STATE / "server.crt").exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                        "-keyout", str(STATE / "server.key"), "-out", str(STATE / "server.crt"),
                        "-subj", "/CN=data", "-addext", "subjectAltName=DNS:data,IP:127.0.0.1",
                        "-addext", "basicConstraints=critical,CA:TRUE"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.chmod(STATE / "server.key", 0o600)
    shutil.copyfile(STATE / "reader.key", CONNECTION / "reader.key")
    os.chmod(CONNECTION / "reader.key", 0o600)
    (CONNECTION / "ca-bundle.pem").write_text(Path(certifi.where()).read_text() + (STATE / "server.crt").read_text())


def sync():
    return sync_docs(STATE, ENDPOINT, ref=os.environ.get("DOCS_REF", "HEAD"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "serve", "sync", "health"])
    args = parser.parse_args()
    if args.command == "serve":
        uvicorn.run(create_data_app(STATE), host="0.0.0.0", port=8850, proxy_headers=False,
                    ssl_certfile=str(STATE / "server.crt"), ssl_keyfile=str(STATE / "server.key"), access_log=False)
    elif args.command == "health":
        response = httpx.get(ENDPOINT + "/health", headers={"Authorization": "Bearer " + (STATE / "reader.key").read_text().strip()}, timeout=3)
        response.raise_for_status()
        if not (STATE / "ready").exists():
            raise SystemExit(1)
    elif args.command == "start":
        initialize()
        (STATE / "ready").unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, __file__, "serve"])
        def stop(*_):
            process.terminate()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Data service stopped during startup")
                try:
                    response = httpx.get(ENDPOINT + "/health", timeout=0.3)
                    if response.status_code == 401:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Data service startup timed out")
            sync()
            (STATE / "ready").touch()
            next_sync = time.monotonic() + SYNC_SECONDS
            while process.poll() is None:
                if time.monotonic() >= next_sync:
                    sync()
                    next_sync = time.monotonic() + SYNC_SECONDS
                time.sleep(1)
            raise SystemExit(process.returncode)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    else:
        result = sync()
        print(json.dumps({'revision': result.get('current', {}).get('revision'), 'sync_error': result['sync_error']}))


if __name__ == "__main__":
    main()
