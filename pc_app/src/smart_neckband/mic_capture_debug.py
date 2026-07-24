from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
import traceback
from typing import Any


_SECRET_WORDS = ("key", "token", "secret", "password", "authorization")


class DebugLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def event(self, name: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": name,
        }
        record.update({key: _safe_value(key, value) for key, value in fields.items()})
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")

    def exception(self, name: str, exc: BaseException, **fields: Any) -> None:
        self.event(
            name,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback="".join(traceback.format_exception(exc)),
            **fields,
        )


def _safe_value(key: str, value: Any) -> Any:
    lower = key.lower()
    if any(word in lower for word in _SECRET_WORDS):
        text = str(value)
        if not text:
            return ""
        return f"<redacted:{len(text)}>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value
