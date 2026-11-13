"""Kill switch: puts the assistant in "out of order" mode instantly, and back.

On if config `out_of_order` is true (ADA_OUT_OF_ORDER=1) OR the state file
(`kill_switch_file`, default data/kill_switch.json) says on. The file is how a running
kiosk is switched without a restart: `make kill-on`, the admin endpoint, and later the
M5.5 Slack bot all write it, and the pipeline reads it before every message and before
every reply sentence (a stat call; re-read only when the file changes).

Fail safe: a state file that exists but can't be read counts as ON.
The state survives restarts on purpose: a reboot must not silently undo a staff decision.
Every flip is appended to `<kill_switch_file>.log` (who, when, why).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KillState:
    on: bool
    source: str  # "config" | "file" | "error" | "default"
    reason: str = ""
    by: str = ""
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


OFF = KillState(False, "default")


class KillSwitch:
    def __init__(self, state_file: Path, config_flag: bool = False) -> None:
        self.path = state_file
        self.config_flag = config_flag
        self._cache: tuple[tuple[int, int, int], KillState] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> KillSwitch:
        return cls(settings.content_path(settings.kill_switch_file), settings.out_of_order)

    @property
    def log_path(self) -> Path:
        return self.path.with_name(self.path.name + ".log")

    def state(self) -> KillState:
        if self.config_flag:
            return KillState(True, "config", "config out_of_order is true")
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return OFF
        except OSError as exc:
            return KillState(True, "error", f"can't stat kill switch file: {exc}")
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            on = data["on"]
            if not isinstance(on, bool):
                raise TypeError("'on' must be true or false")
            state = KillState(
                on, "file", str(data.get("reason", "")), str(data.get("by", "")),
                str(data.get("at", "")),
            )  # fmt: skip
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("kill switch file %s unreadable (%s): treating as ON", self.path, exc)
            return KillState(True, "error", f"kill switch file unreadable: {exc}")
        self._cache = (key, state)
        return state

    def is_on(self) -> bool:
        return self.state().on

    def set(self, on: bool, reason: str = "", by: str = "") -> KillState:
        """Write the state file atomically and log the flip. Returns the effective state."""
        at = datetime.now(UTC).isoformat(timespec="seconds")
        record = {"on": on, "reason": reason, "by": by, "at": at}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, self.path)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self._cache = None
        log.warning("kill switch %s by %r: %s", "ON" if on else "OFF", by, reason)
        return self.state()
