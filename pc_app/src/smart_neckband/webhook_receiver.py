from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from threading import Thread
from typing import Callable

from .webhook_models import MAX_WEBHOOK_BODY_BYTES, ReplyEvent, normalize_callback_path
from .webhook_store import ReplyConflictError, WebhookStore


LOGGER = logging.getLogger(__name__)


class ReplyWebhookServer:
    def __init__(
        self,
        *,
        store: WebhookStore,
        host: str,
        port: int,
        path: str,
        reply_callback: Callable[[ReplyEvent], None] | None = None,
        debug_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.host = host
        self.port = port
        self.path = normalize_callback_path(path)
        self.reply_callback = reply_callback
        self.debug_callback = debug_callback
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def bound_port(self) -> int | None:
        if self._server is None:
            return None
        return int(self._server.server_address[1])

    def start(self) -> int:
        if self.running:
            return self.bound_port or self.port
        handler = self._handler_class()
        server = ThreadingHTTPServer((self.host, self.port), handler)
        server.daemon_threads = True
        self._server = server
        self._thread = Thread(
            target=server.serve_forever,
            name="ReplyWebhookServer",
            daemon=True,
        )
        self._thread.start()
        self._debug(f"回复回调监听已启动：http://{self.host}:{self.bound_port}{self.path}")
        return self.bound_port or self.port

    def stop(self, timeout: float = 3.0) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=timeout)
        self._server = None
        self._thread = None
        self._debug("回复回调监听已停止")

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                if self.path != owner.path:
                    self._send_json(404, {"error": "not_found"})
                    return
                content_type = self.headers.get("Content-Type", "")
                if not content_type.lower().startswith("application/json"):
                    self._send_json(400, {"error": "invalid_request"})
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self._send_json(400, {"error": "invalid_request"})
                    return
                if not 0 < content_length <= MAX_WEBHOOK_BODY_BYTES:
                    self._send_json(400, {"error": "invalid_request"})
                    return
                body = self.rfile.read(content_length)
                try:
                    event = ReplyEvent.from_dict(json.loads(body.decode("utf-8")))
                    inserted = owner.store.save_reply(event)
                except ReplyConflictError:
                    LOGGER.exception("Conflicting Agent reply callback")
                    self._send_json(409, {"error": "reply_id_conflict"})
                    return
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    LOGGER.exception("Invalid Agent reply callback")
                    self._send_json(400, {"error": "invalid_request"})
                    return

                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                if inserted:
                    owner._debug(
                        f"收到 Agent 最终回复：instruction_id={event.instruction_id} "
                        f"reply_id={event.reply_id}"
                    )
                    if owner.reply_callback is not None:
                        try:
                            owner.reply_callback(event)
                        except Exception:
                            LOGGER.exception("Reply callback notification failed")
                else:
                    owner._debug(f"重复回复已去重：reply_id={event.reply_id}")

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                self._send_json(404, {"error": "not_found"})

            def log_message(self, format: str, *args: object) -> None:
                LOGGER.debug("Reply webhook HTTP: " + format, *args)

            def _send_json(self, status: int, payload: dict[str, str]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def _debug(self, message: str) -> None:
        LOGGER.debug(message)
        if self.debug_callback is not None:
            self.debug_callback(message)
