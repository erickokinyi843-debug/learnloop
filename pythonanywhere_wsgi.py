from __future__ import annotations

import io
import os
import sys
import threading
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault(
    "LEARNLOOP_DATA_DIR",
    str(Path.home() / "learnloop-data"),
)
os.environ.setdefault("COOKIE_SECURE", "1")

import server


_initialization_lock = threading.Lock()
_initialized = False


class WSGIResponseHandler(server.LearnloopHandler):
    def __init__(self, environ: dict[str, object]):
        self.command = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/")) or "/"
        raw_path = environ.get("RAW_URI") or environ.get("REQUEST_URI")
        if raw_path:
            self.path = str(raw_path)
        else:
            self.path = quote(path, safe="/%:@")
            query = str(environ.get("QUERY_STRING", ""))
            if query:
                self.path += f"?{query}"

        self.headers = Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-").title()
                self.headers[name] = str(value)
        for key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            value = environ.get(key)
            if value:
                self.headers[key.replace("_", "-").title()] = str(value)

        self.client_address = (str(environ.get("REMOTE_ADDR", "")), 0)
        self.rfile = environ["wsgi.input"]
        self.wfile = io.BytesIO()
        self.response_status = HTTPStatus.INTERNAL_SERVER_ERROR
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, code: int, message: str | None = None) -> None:
        self.response_status = HTTPStatus(code)

    def send_header(self, keyword: str, value: str) -> None:
        self.response_headers.append((keyword, value))

    def end_headers(self) -> None:
        for keyword, value in server.SECURITY_HEADERS:
            self.send_header(keyword, value)
        for keyword, value in self.cors_headers():
            self.send_header(keyword, value)


def application(environ: dict[str, object], start_response):
    global _initialized
    if not _initialized:
        with _initialization_lock:
            if not _initialized:
                server.initialize_database()
                _initialized = True

    handler = WSGIResponseHandler(environ)
    handler.dispatch(handler.command)
    status = f"{handler.response_status.value} {handler.response_status.phrase}"
    start_response(status, handler.response_headers)
    return [handler.wfile.getvalue()]
