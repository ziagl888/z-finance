"""Structured log: one JSON object per line on stderr, optionally appended to a file.

Callers pass counts, ids and short error texts only — never credentials, the subject secret, mail
text or the model's output.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


class Log:
    """Writes events as JSON lines."""

    def __init__(self, stream: TextIO | None = None, file: str = "") -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._file = Path(file) if file else None
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: str, level: str = "info", **fields: Any) -> None:
        """Record ``event`` with ``fields``."""
        record = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "level": level, "event": event, **fields}
        self.events.append(record)
        line = json.dumps(record, ensure_ascii=False, default=str)
        print(line, file=self._stream, flush=True)
        if self._file is not None:
            try:
                with self._file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                print(
                    json.dumps({"event": "log_file_failed", "level": "warning", "error": type(exc).__name__}),
                    file=self._stream,
                    flush=True,
                )
