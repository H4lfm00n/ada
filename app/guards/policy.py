"""content/guard_policy.yaml: rule patterns, limits, and block -> line category mapping."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from app.guards.normalize import fold

log = logging.getLogger(__name__)

Side = Literal["input", "output"]
GUARD_FALLBACK = "guard_fallback"


class PolicyError(ValueError):
    """guard_policy.yaml is malformed."""


@dataclass(slots=True)
class GuardPolicy:
    max_input_chars: int
    injection_patterns: list[re.Pattern[str]]
    block_visitor_pii: bool
    student_id_re: re.Pattern[str] | None
    block_unlisted_contacts: bool
    canaries: list[str]
    blocklist: set[str]  # folded words / phrases
    input_lines: dict[str, str]
    output_lines: dict[str, str]
    error_line: str
    unknown_line: str
    priority: list[str]
    todo_categories: set[str] = field(default_factory=set)  # mapped but not in lines.yaml

    @classmethod
    def load(cls, path: Path, local_blocklist: Path | None = None) -> GuardPolicy:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            inp, out, lines = data["input"], data["output"], data["lines"]
            patterns = [re.compile(p, re.IGNORECASE) for p in inp.get("injection_patterns", [])]
            sid = inp.get("student_id_pattern")
            words = {fold(w) for w in data.get("blocklist", []) if fold(str(w))}
            policy = cls(
                max_input_chars=int(inp["max_chars"]),
                injection_patterns=patterns,
                block_visitor_pii=bool(inp.get("block_visitor_pii", True)),
                student_id_re=re.compile(sid) if sid else None,
                block_unlisted_contacts=bool(out.get("block_unlisted_contacts", True)),
                canaries=[str(c) for c in out.get("canaries", [])],
                blocklist=words,
                input_lines={str(k): str(v) for k, v in (lines.get("input") or {}).items()},
                output_lines={str(k): str(v) for k, v in (lines.get("output") or {}).items()},
                error_line=str(lines.get("error", GUARD_FALLBACK)),
                unknown_line=str(lines.get("unknown", GUARD_FALLBACK)),
                priority=[str(p) for p in data.get("priority", [])],
            )
        except (KeyError, TypeError, re.error, yaml.YAMLError) as exc:
            raise PolicyError(f"{path.name}: {exc}") from exc
        if local_blocklist is not None and local_blocklist.is_file():
            for ln in local_blocklist.read_text(encoding="utf-8").splitlines():
                if ln.strip() and not ln.lstrip().startswith("#") and fold(ln):
                    policy.blocklist.add(fold(ln))
        return policy

    def check_lines(self, available: set[str]) -> None:
        """Remember which mapped categories lines.yaml lacks (they fall back, with a log)."""
        mapped = {*self.input_lines.values(), *self.output_lines.values()}
        mapped |= {self.error_line, self.unknown_line}
        self.todo_categories = mapped - available
        if GUARD_FALLBACK not in available:
            raise PolicyError("lines.yaml has no guard_fallback category")
        for cat in sorted(self.todo_categories):
            log.warning("guard policy maps to %r, which lines.yaml lacks (TODO designer)", cat)

    def pick_code(self, codes: list[str]) -> str:
        """The most important of several guard codes ("S10,S11" -> "S11")."""
        for p in self.priority:
            if p in codes:
                return p
        return codes[0] if codes else "unknown"

    def line_for(self, side: Side, code: str) -> str:
        """The lines.yaml category for a block. Missing categories -> guard_fallback."""
        if code in ("error", "timeout"):
            cat = self.error_line
        elif side == "input":
            cat = self.input_lines.get(code, self.unknown_line)
        else:
            cat = self.output_lines.get(code, self.output_lines.get("default", GUARD_FALLBACK))
        return GUARD_FALLBACK if cat in self.todo_categories else cat
