"""Owned loopback HTTP fixture for real, model-free Windows bridge checks."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError):
            # An intentional client timeout can reset the keepalive connection.
            pass

    def log_message(self, *_args):
        pass

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        if self.path == "/slow":
            time.sleep(2)
        if self.path == "/loading":
            self.respond(503, {"error": {"message": "Loading model"}})
        else:
            self.respond(200, {"ready": True})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/error":
            self.respond(422, {"error": {"message": "fixture rejected request"}})
            return
        payload = json.loads(body)
        self.respond(200, {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                           "marker": payload["marker"], "model": payload.get("model")})


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        worker.start()
        print(json.dumps({"pid": os.getpid(), "port": server.server_port}), flush=True)
        try:
            sys.stdin.readline()
        finally:
            server.shutdown()
            worker.join(5)
            if worker.is_alive():
                raise RuntimeError("HTTP fixture did not stop")


if __name__ == "__main__":
    main()
