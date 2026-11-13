"""Transcript logging (brief section 5.7): text only, local only, one file per visitor.

    data/transcripts/2026-11-12/153012-3f9c2a1b7e44-v2.jsonl

One JSON object per line: a `visit_start`, then one `turn` per message (visitor text,
what was shown, where it came from, every guard decision with timings, which lab docs
were used), and events such as `goodbye`, `timeout`, `kill_switch`.

Privacy:
- No audio, no video, nothing leaves the machine. Folders are 0700, files 0600.
- Phone numbers, emails, and student IDs a visitor types are redacted before writing,
  except contacts that are in the lab docs (the output-guard allowlist).
- Files older than `transcript_retention_days` are deleted at startup (`prune`) and by
  `make transcripts-prune`.
A logging failure is logged and ignored: it never stops the kiosk from answering.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.guards.contacts import ContactAllowlist, find_contacts

if TYPE_CHECKING:
    from app.pipeline import TurnResult

log = logging.getLogger(__name__)

Now = Callable[[], datetime]
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def make_redactor(
    allowlist: ContactAllowlist, student_id_re: re.Pattern[str] | None
) -> Callable[[str], str]:
    def redact(text: str) -> str:
        for c in sorted(find_contacts(text), key=lambda c: -len(c.raw)):
            if c.kind in ("phone", "email") and not allowlist.allows(c):
                text = text.replace(c.raw, f"[{c.kind}]")
        if student_id_re is not None:
            text = student_id_re.sub("[student-id]", text)
        return text

    return redact


class TranscriptLogger:
    def __init__(
        self,
        root: Path,
        retention_days: int = 30,
        redact: Callable[[str], str] = lambda s: s,
        now: Now = lambda: datetime.now().astimezone(),
    ) -> None:
        self.root = root
        self.retention_days = retention_days
        self.redact = redact
        self._now = now
        self._open: dict[str, Path] = {}  # session id -> current visit's file
        self._visits: dict[str, int] = {}

    # -- writing ------------------------------------------------------------------------

    def _path_for(self, session_id: str) -> Path:
        path = self._open.get(session_id)
        if path is None:
            n = self._visits[session_id] = self._visits.get(session_id, 0) + 1
            now = self._now()
            day = self.root / now.strftime("%Y-%m-%d")
            day.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = day / f"{now.strftime('%H%M%S')}-{session_id}-v{n}.jsonl"
            self._open[session_id] = path
            self._write(path, {"type": "visit_start", "session": session_id, "visit": n})
        return path

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        record = {"ts": self._now().isoformat(timespec="milliseconds"), **record}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _safe(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # never let logging break a reply
            log.exception("transcript write failed")

    def log_turn(self, r: TurnResult) -> None:
        def write() -> None:
            rec: dict[str, Any] = {
                "type": "turn",
                "user": self.redact(r.user_text),
                "shown": r.sentences,
                "source": r.source,
                "input_guard": r.input_guard.to_dict() if r.input_guard else None,
                "output_guard": [d.to_dict() for d in r.output_guard],
                "blocked_sentence": self.redact(r.blocked_sentence) if r.blocked_sentence else None,
                "kill_switch": r.kill_switch,
                "docs": [c.metadata.get("id", c.source_file) for c in r.retrieved],
                "retrieval_s": r.retrieval_s,
                "ttft_s": r.ttft_s,
                "first_sentence_s": r.first_sentence_s,
                "total_s": r.total_s,
                "error": r.error,
                "added_to_history": r.added_to_history,
            }
            self._write(self._path_for(r.session_id), rec)

        self._safe(write)

    def log_event(self, session_id: str, event: str, **data: Any) -> None:
        self._safe(lambda: self._write(self._path_for(session_id), {"type": event, **data}))

    def end_visit(self, session_id: str, event: str, **data: Any) -> None:
        """Record why the visit ended; the session's next message starts a new file."""
        if session_id in self._open:
            self.log_event(session_id, event, **data)
        self._open.pop(session_id, None)

    # -- retention ----------------------------------------------------------------------

    def prune(self) -> int:
        """Delete day folders older than retention_days. Returns how many were removed."""
        if not self.root.is_dir():
            return 0
        cutoff = (self._now() - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        removed = 0
        for d in self.root.iterdir():
            if d.is_dir() and _DAY_RE.match(d.name) and d.name < cutoff:
                shutil.rmtree(d)
                removed += 1
        return removed
