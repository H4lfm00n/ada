"""Idle life (PHASE2-SPEC 3.1, M5a): which idle act the kiosk plays, and when.

Content: `content/idle_acts.yaml` (designer). This module only decides *which* act and
*when*; the screen (ui/blob.js) owns the motion for each act id.

Rules (spec 3.1, PM answer 8.3, idle_acts.yaml header):
  - Visual only. Nothing here makes sound, and nothing is generated live: captions are
    the designer's text (or facts from an approved card).
  - Never the same act twice in a row; weighted random pick; a 45-120 s gap (from the
    file's `rotation`) between acts, breathing in between.
  - Acts with `after_idle_s` wait until the screen has been quiet that long.
  - `reduced_motion: skip` acts don't run when the screen prefers reduced motion.
  - `enabled: false` acts never run.
  - Acts that need content which isn't approved or isn't built yet are left out, with the
    reason kept in `IdleCatalog.unavailable` (shown by the server at startup):
      * `caption_from: content/cards.yaml#<card>.<field>` needs that card `approved: yes`.
      * `from_docs:` needs the lab_docs event reader (M5c), so it is off for now.

The scheduler is pure (clock passed in, random.Random injected) so tests are exact.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CAPTION_FROM_RE = re.compile(r"^(?P<file>[\w./-]+)#(?P<card>[\w-]+)\.(?P<field>\w+)$")
REDUCED_VALUES = frozenset({"gentle", "skip"})


class IdleActsError(ValueError):
    """content/idle_acts.yaml is malformed."""


@dataclass(frozen=True, slots=True)
class IdleAct:
    id: str
    name: str
    duration_s: float
    weight: float
    blob: str = ""
    caption: str | None = None
    captions: tuple[str, ...] = ()  # pick one at random (caption_from)
    caption_prefix: str = ""
    after_idle_s: float = 0.0
    reduced_motion: str | None = None  # None | gentle | skip

    def caption_for(self, rng: random.Random) -> str | None:
        if self.captions:
            return self.caption_prefix + rng.choice(self.captions)
        return self.caption


@dataclass(frozen=True, slots=True)
class Rotation:
    min_gap_s: float = 45.0
    max_gap_s: float = 120.0
    default_act: str = "breathing"
    no_repeat: bool = True


@dataclass(slots=True)
class IdleCatalog:
    rotation: Rotation
    acts: dict[str, IdleAct]  # playable acts only (weight > 0, enabled, content available)
    unavailable: dict[str, str] = field(default_factory=dict)  # act id -> why it's off

    @property
    def playable(self) -> list[IdleAct]:
        return list(self.acts.values())


def _approved(value: Any) -> bool:
    # YAML 1.1 reads a bare `yes` as True; the designer's file says `approved: yes`.
    return value is True or (isinstance(value, str) and value.strip().lower() == "yes")


def _card_field(content_root: Path, ref: str) -> tuple[list[str] | None, str | None]:
    """Resolve `content/cards.yaml#ai_fact.facts` -> (texts, None) or (None, reason)."""
    m = CAPTION_FROM_RE.match(ref)
    if not m:
        return None, f"caption_from {ref!r} is not <file>#<card>.<field>"
    path = content_root / m["file"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, f"caption_from: can't read {m['file']} ({exc})"
    cards = data.get("cards") if isinstance(data, dict) else None
    card = next((c for c in cards or [] if isinstance(c, dict) and c.get("id") == m["card"]), None)
    if card is None:
        return None, f"caption_from: no card {m['card']!r} in {m['file']}"
    if not _approved(card.get("approved")):
        return None, f"card {m['card']!r} is not approved by the PM yet ({m['file']})"
    texts = card.get(m["field"])
    if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
        return None, f"caption_from: {m['card']}.{m['field']} is not a list of text"
    return [t.strip() for t in texts if t.strip()], None


def _num(raw: dict[str, Any], key: str, default: float, where: str) -> float:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise IdleActsError(f"{where}: {key} must be a number >= 0")
    return float(v)


def load_idle_acts(path: Path, content_root: Path, bot_name: str = "Ada") -> IdleCatalog:
    """Parse and validate idle_acts.yaml. `content_root` resolves caption_from paths."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise IdleActsError(f"{path.name}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("acts"), list):
        raise IdleActsError(f"{path.name}: needs an 'acts' list")

    rot_raw = data.get("rotation") or {}
    if not isinstance(rot_raw, dict):
        raise IdleActsError(f"{path.name}: 'rotation' must be a mapping")
    rotation = Rotation(
        min_gap_s=_num(rot_raw, "min_gap_s", 45, "rotation"),
        max_gap_s=_num(rot_raw, "max_gap_s", 120, "rotation"),
        default_act=str(rot_raw.get("default_act", "breathing")),
        no_repeat=bool(rot_raw.get("no_repeat", True)),
    )
    if rotation.max_gap_s < rotation.min_gap_s:
        raise IdleActsError("rotation: max_gap_s is below min_gap_s")

    acts: dict[str, IdleAct] = {}
    unavailable: dict[str, str] = {}
    seen: set[str] = set()
    for i, raw in enumerate(data["acts"]):
        where = f"{path.name} act #{i + 1}"
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise IdleActsError(f"{where}: needs an 'id'")
        aid = raw["id"]
        if aid in seen:
            raise IdleActsError(f"{where}: duplicate id {aid!r}")
        seen.add(aid)
        rm = raw.get("reduced_motion")
        if rm is not None and rm not in REDUCED_VALUES:
            raise IdleActsError(f"{where}: reduced_motion must be gentle or skip")
        caption = raw.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise IdleActsError(f"{where}: caption must be text")
        weight = _num(raw, "weight", 1, where)
        duration = _num(raw, "duration_s", 5, where)

        if aid == rotation.default_act or weight == 0:
            continue  # breathing is the state between acts, not an act
        if raw.get("enabled") is False:
            unavailable[aid] = "enabled: false in idle_acts.yaml"
            continue
        if duration <= 0:
            raise IdleActsError(f"{where}: duration_s must be > 0 for a playable act")

        captions: tuple[str, ...] = ()
        if raw.get("caption_from"):
            texts, why = _card_field(content_root, str(raw["caption_from"]))
            if why:
                unavailable[aid] = why
                continue
            captions = tuple(texts or ())
        if raw.get("from_docs"):
            unavailable[aid] = f"needs the lab_docs event reader (M5c): {raw['from_docs']}"
            continue

        if caption:
            caption = caption.replace("{{bot_name}}", bot_name)
        acts[aid] = IdleAct(
            id=aid,
            name=str(raw.get("name", aid)),
            duration_s=duration,
            weight=weight,
            blob=str(raw.get("blob", "")),
            caption=caption,
            captions=captions,
            caption_prefix=str(raw.get("caption_prefix", "")),
            after_idle_s=_num(raw, "after_idle_s", 0, where),
            reduced_motion=rm,
        )
    if rotation.default_act not in seen:
        raise IdleActsError(f"rotation.default_act {rotation.default_act!r} is not an act")
    return IdleCatalog(rotation=rotation, acts=acts, unavailable=unavailable)


class IdlePicker:
    """Weighted random pick with no back-to-back repeat."""

    def __init__(self, catalog: IdleCatalog, rng: random.Random | None = None) -> None:
        self.catalog = catalog
        self.rng = rng or random.Random()
        self.last: str | None = None

    def eligible(self, quiet_for_s: float, reduced: bool) -> list[IdleAct]:
        out = []
        for act in self.catalog.playable:
            if reduced and act.reduced_motion == "skip":
                continue
            if quiet_for_s < act.after_idle_s:
                continue
            if self.catalog.rotation.no_repeat and act.id == self.last:
                continue
            out.append(act)
        return out

    def pick(self, quiet_for_s: float, reduced: bool = False) -> IdleAct | None:
        acts = self.eligible(quiet_for_s, reduced)
        if not acts:
            return None
        act = self.rng.choices(acts, weights=[a.weight for a in acts], k=1)[0]
        self.last = act.id
        return act

    def gap(self) -> float:
        r = self.catalog.rotation
        return self.rng.uniform(r.min_gap_s, r.max_gap_s)


@dataclass(frozen=True, slots=True)
class IdleCue:
    """One act to play now. Sent to the screen as the `idle_act` event."""

    act: IdleAct
    caption: str | None

    def event(self) -> dict[str, Any]:
        return {
            "type": "idle_act",
            "id": self.act.id,
            "duration_s": self.act.duration_s,
            "caption": self.caption,
            "reduced_motion": self.act.reduced_motion,
        }


class IdleScheduler:
    """When to play the next act. The server calls `step(now, quiet=...)` every tick.

    quiet = a screen is connected and nobody is there (no session, no turn, kill switch
    off, Ollama reachable). Any visitor interaction calls `interrupt(now)`: the screen has
    already stopped the act itself; the scheduler restarts the quiet clock.
    """

    def __init__(self, picker: IdlePicker) -> None:
        self.picker = picker
        self.quiet_since: float | None = None
        self.next_at: float | None = None
        self.playing_until: float = 0.0

    def interrupt(self, now: float) -> None:
        self.quiet_since = None
        self.next_at = None
        self.playing_until = now

    def step(self, now: float, *, quiet: bool, reduced: bool = False) -> IdleCue | None:
        if not quiet:
            if self.quiet_since is not None:
                self.interrupt(now)
            return None
        if self.quiet_since is None:
            self.quiet_since = now
            self.next_at = now + self.picker.gap()
            return None
        if self.next_at is None or now < self.next_at or now < self.playing_until:
            return None
        act = self.picker.pick(now - self.quiet_since, reduced)
        if act is None:  # nothing eligible yet (e.g. only after_idle_s acts): try later
            self.next_at = now + self.picker.gap()
            return None
        self.playing_until = now + act.duration_s
        self.next_at = self.playing_until + self.picker.gap()
        return IdleCue(act, act.caption_for(self.picker.rng))
