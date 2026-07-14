from __future__ import annotations

from pathlib import Path
from threading import Lock


class RawBinaryRecorder:
    """Append raw serial bytes to disk from the serial worker thread."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("ab")
        self._lock = Lock()

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._file.write(data)
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()

    def __enter__(self) -> RawBinaryRecorder:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        del exc_type, exc, tb
        self.close()
