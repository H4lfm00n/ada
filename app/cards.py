"""Cards (M5b server half, PHASE2-SPEC 2): what Ada puts on the screen, and the check-in.

Content: `content/cards.yaml` (designer writes, PM approves every card and URL). The UI
(`ui/cards.js`, PMC1) only draws what the server sends. This module decides:

  * Which cards exist: enabled cards only. An enabled card whose `approved` isn't `yes` is a
    startup error (refused, not skipped). QR cards need an `https://` URL, and the URL must
    pass the same contact allowlist as Ada's replies (`check_urls`). Only QR cards carry a URL.
  * Tags: the model asks for a card with `<card id="..."/>` at the end of a reply. Tags are
    stripped from every sentence before the output guard, captions, history and transcripts
    (`strip_card_tags`). Unknown or disabled IDs are dropped.
  * Which card to show for a reply (`CardCatalog.pick`): the first valid tag; else, for a
    canned line, a card listed in `show_with_lines` (campus help with the emergency lines);
    else the backup trigger (spec 2.1): the visitor's question contains one of the card's
    `triggers` AND a retrieved doc section is in its `doc_sections`.
  * Placeholder QR cards (PM, 2026-11-18): `placeholder: true` shows `placeholder_note` on the
    card, but only while `url` is still `defaults.placeholder_url`. Swapping in the real URL
    is a one-line change; the note disappears with it.
  * The check-in loop (spec 2.2) as a small state machine on an injected clock (`CardStage`):
    intro -> asking -> yes | no -> waiting -> asking once more -> resting; a new question makes
    the card mini; timeout, reset, and the kill switch hide it.
  * Yes / no from typed text: keywords only (`classify_reply`). Anything else is a new
    question. Taps on the card's chips (`card_reply`) are treated exactly the same.

Card lines (`say_before`, `confirm`, `on_yes`, ...) are PM-approved catalog text, like the
canned lines in lines.yaml: they are not generated, so they skip the output guard model (D33).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

CARD_TYPES = frozenset({"qr", "directions", "list", "spotlight", "checklist", "contact", "fun"})
# Fields the UI may receive (PMC1). Everything else (say_* lines, triggers, sources, notes)
# stays on the server.
UI_FIELDS = (
    "title", "subtitle", "eyebrow", "url", "lines", "hours", "you_are_here", "items",
    "fallback_items", "footer", "confirm", "qr_caption",
)  # fmt: skip
TEXT_FIELDS = ("say_before", "confirm", "on_yes", "on_no", "on_no_or_timeout", "empty_say")

# <card id="qr_discord"/>, <card id=qr_discord>, </card>, and a cut-off "<card id=..." at the end
TAG_RE = re.compile(r"<\s*/?\s*card\b[^>]*>", re.IGNORECASE)
OPEN_TAIL_RE = re.compile(r"<\s*/?\s*card\b[^>]*$", re.IGNORECASE)
ID_RE = re.compile(r"""\bid\s*=\s*["']?([A-Za-z0-9_-]+)""", re.IGNORECASE)


class CardsError(ValueError):
    """content/cards.yaml is malformed, or an enabled card isn't approved or safe."""


class Allowlist(Protocol):
    def blocked(self, text: str) -> list[Any]: ...


class ChunkLike(Protocol):
    source_file: str
    heading: str


# -- tags --------------------------------------------------------------------------------


def strip_card_tags(text: str) -> tuple[str, list[str]]:
    """Remove every card tag from one sentence. Returns (clean text, requested IDs)."""
    ids = [m.group(1) for t in TAG_RE.finditer(text) if (m := ID_RE.search(t.group(0)))]
    out = TAG_RE.sub(" ", text)
    if tail := OPEN_TAIL_RE.search(out):
        if m := ID_RE.search(tail.group(0)):
            ids.append(m.group(1))
        out = out[: tail.start()]
    out = re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    return out, ids


# -- yes / no ----------------------------------------------------------------------------

_YES = {
    "yes", "yeah", "yep", "yup", "ya", "yea", "sure", "ok", "okay", "done", "got it",
    "i got it", "scanned", "scanned it", "i scanned it", "i did", "did", "yes i did",
    "all good", "perfect", "nice", "cool", "great", "it worked", "worked", "i have it",
    "got it thanks", "yes thanks", "yes thank you", "joined", "i joined",
}  # fmt: skip
_NO = {
    "no", "nope", "nah", "not yet", "no not yet", "wait", "one sec", "one second",
    "hold on", "just a sec", "give me a sec", "give me a second", "a sec", "sec",
    "not really", "still scanning", "it didnt work", "didnt work", "it doesnt work",
    "doesnt work", "not working", "hang on", "almost",
}  # fmt: skip
_TRAILING = re.compile(r"\s+(?:thanks|thank you|thx|ada)$")


def classify_reply(text: str) -> str | None:
    """'yes' / 'no' for a short answer to "did you scan it?", else None (a new question)."""
    t = re.sub(r"[^\w\s]", "", text.lower())  # also drops apostrophes: "didn't" -> "didnt"
    t = re.sub(r"\s+", " ", t).strip()
    if not t or "?" in text or len(t.split()) > 6:
        return None
    for cand in (t, _TRAILING.sub("", t)):
        if cand in _YES:
            return "yes"
        if cand in _NO:
            return "no"
    first = t.split()[0]
    if first in {"yes", "yeah", "yep", "yup"} and len(t.split()) <= 4:
        return "yes"
    if first in {"no", "nope", "nah"} and len(t.split()) <= 4:
        return "no"
    return None


# -- catalog -----------------------------------------------------------------------------


def _approved(value: Any) -> bool:
    # YAML 1.1 reads a bare `yes` as True; the designer's file says `approved: yes`.
    return value is True or (isinstance(value, str) and value.strip().lower() == "yes")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    type: str
    data: dict[str, Any]  # what the UI gets in show_card
    say_before: str = ""
    confirm: str = ""
    on_yes: str = ""
    on_no: str = ""
    on_no_or_timeout: str = ""
    auto_hide_s: float = 60.0
    confirm_after_s: float = 6.0
    retry_after_s: float = 10.0
    mini_when_new_question: bool = True
    triggers: tuple[str, ...] = ()  # normalized, for matching
    trigger_words: tuple[str, ...] = ()  # as written, for the prompt's card list
    doc_sections: tuple[tuple[str, str | None], ...] = ()
    show_with_lines: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    prompt_hint: str = ""
    placeholder: bool = False

    @property
    def checks_in(self) -> bool:
        return self.type == "qr" and bool(self.confirm)

    def ui_data(self, rng: random.Random) -> dict[str, Any]:
        d = dict(self.data)
        if self.facts:  # fun card: one reviewed fact per showing
            d["fact"] = rng.choice(self.facts)
        return d

    def matches_docs(self, chunks: list[Any]) -> bool:
        if not self.doc_sections:
            return True
        for c in chunks:
            name = Path(str(getattr(c, "source_file", ""))).name
            heading = str(getattr(c, "heading", "") or "").lower()
            for file, head in self.doc_sections:
                if name == file and (head is None or heading.startswith(head.lower())):
                    return True
        return False

    def triggered_by(self, question: str) -> bool:
        q = f" {_norm(question)} "
        return any(f" {t} " in q for t in self.triggers)


@dataclass(frozen=True, slots=True)
class Pick:
    card: Card
    via: str  # "tag" | "line" | "backup"


@dataclass(slots=True)
class CardCatalog:
    cards: dict[str, Card]  # enabled + approved only, catalog order
    unavailable: dict[str, str] = field(default_factory=dict)  # id -> why it's off
    warnings: list[str] = field(default_factory=list)
    placeholder_url: str = ""

    # ---- prompt
    def prompt_list(self) -> str:
        """The {{card_list}} slot: one line per card the model may ask for."""
        lines = []
        for c in self.cards.values():
            if c.show_with_lines and not c.triggers and not c.prompt_hint:
                hint = (
                    "emergency and crisis contacts. "
                    "Use whenever you tell someone to call 911 or 988."
                )
            else:
                hint = c.prompt_hint or (
                    str(c.data.get("title", c.id)).rstrip(".?! ")
                    + (" (QR code)" if c.type == "qr" else "")
                    + (f". Use when someone asks about: {', '.join(c.trigger_words[:5])}."
                       if c.triggers else ".")
                )  # fmt: skip
            lines.append(f"- {c.id}: {hint}")
        return "\n".join(lines) if lines else "none"  # DES52: the prompt's rules handle it

    # ---- safety
    def check_urls(self, allowlist: Allowlist) -> None:
        """Every QR URL must pass the contact allowlist built from the lab docs."""
        bad = [f"{c.id}: {c.data['url']}" for c in self.cards.values()
               if c.type == "qr" and allowlist.blocked(c.data["url"])]  # fmt: skip
        if bad:
            raise CardsError(
                "QR card URL(s) not in the lab docs (add the link to lab_docs/ and run "
                "`make ingest`, or disable the card): " + "; ".join(bad)
            )

    # ---- choosing
    def pick(
        self, tag_ids: list[str], question: str, retrieved: list[Any], source: str
    ) -> Pick | None:
        for cid in tag_ids:
            if cid in self.cards:
                return Pick(self.cards[cid], "tag")
        if tag_ids:  # the model asked for something that doesn't exist: show nothing
            return None
        if source.startswith("line:"):
            cat = source.split(":", 1)[1]
            for c in self.cards.values():
                if cat in c.show_with_lines:
                    return Pick(c, "line")
            return None
        if source != "model":
            return None
        for c in self.cards.values():
            if c.triggers and c.triggered_by(question) and c.matches_docs(retrieved):
                return Pick(c, "backup")
        return None


def _doc_section(raw: str, where: str) -> tuple[str, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        raise CardsError(f"{where}: doc_sections entries must be 'file.md' or 'file.md#Heading'")
    file, _, head = raw.partition("#")
    return file.strip(), (head.strip() or None)


def _num(v: Any, where: str, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise CardsError(f"{where}: {key} must be a number >= 0")
    return float(v)


def load_cards(path: Path, bot_name: str = "Ada") -> CardCatalog:
    """Parse and validate content/cards.yaml. Raises CardsError on anything unsafe."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CardsError(f"{path.name}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise CardsError(f"{path.name}: needs a 'cards' list")
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise CardsError(f"{path.name}: 'defaults' must be a mapping")
    placeholder_url = str(defaults.get("placeholder_url") or "")

    cards: dict[str, Card] = {}
    unavailable: dict[str, str] = {}
    warnings: list[str] = []
    seen: set[str] = set()

    def fill(s: Any) -> str:
        return str(s or "").replace("{{bot_name}}", bot_name).strip()

    for i, raw in enumerate(data["cards"]):
        where = f"{path.name} card #{i + 1}"
        if isinstance(raw, dict) and isinstance(raw.get("id"), bool):
            raise CardsError(f"{where}: id reads as a YAML true/false (yes/no/on/off); quote it")
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise CardsError(f"{where}: needs an 'id'")
        cid = raw["id"]
        where = f"{path.name} card {cid!r}"
        if cid in seen:
            raise CardsError(f"{where}: duplicate id")
        seen.add(cid)
        ctype = raw.get("type")
        if ctype not in CARD_TYPES:
            raise CardsError(f"{where}: type must be one of {sorted(CARD_TYPES)}")
        if raw.get("enabled") is not True:
            unavailable[cid] = "enabled: false"
            continue
        if not _approved(raw.get("approved")):
            raise CardsError(f"{where}: enabled but not `approved: yes` (PM approves every card)")

        url = raw.get("url")
        if ctype == "qr":
            if not isinstance(url, str) or not re.fullmatch(r"https://[^\s<>\"']+", url.strip()):
                raise CardsError(f"{where}: a QR card needs an https:// URL (got {url!r})")
            url = url.strip()
        elif url is not None:
            raise CardsError(f"{where}: only QR cards may have a url")

        placeholder = False
        if raw.get("placeholder") is True:
            if url and url == placeholder_url:
                placeholder = True
                if not str(raw.get("placeholder_note") or "").strip():
                    raise CardsError(f"{where}: placeholder card needs a placeholder_note")
            else:
                warnings.append(
                    f"{cid}: `placeholder: true` but the url is no longer the placeholder; "
                    "the note is hidden (remove the line when convenient)"
                )

        ui: dict[str, Any] = {"id": cid, "type": ctype}
        for key in UI_FIELDS:
            if raw.get(key) is not None:
                v = raw[key]
                ui[key] = fill(v) if isinstance(v, str) else v
        if ctype == "qr":
            ui["url"] = url
        if placeholder:
            ui["placeholder"] = True
            ui["placeholder_note"] = fill(raw["placeholder_note"])
        auto_hide = _num(
            raw.get("auto_hide_s", defaults.get("auto_hide_s", 60)), where, "auto_hide_s"
        )
        ui["auto_hide_s"] = auto_hide

        triggers = tuple(
            _norm(t) for t in raw.get("triggers") or [] if isinstance(t, str) and t.strip()
        )
        docs = tuple(_doc_section(d, where) for d in raw.get("doc_sections") or [])
        facts = tuple(fill(f) for f in raw.get("facts") or [] if isinstance(f, str) and f.strip())
        if ctype == "fun" and not facts:
            raise CardsError(f"{where}: a fun card needs facts")
        text = {k: fill(raw.get(k)) for k in TEXT_FIELDS}

        def setting(key: str, default: Any, raw: dict[str, Any] = raw) -> Any:
            return raw.get(key, defaults.get(key, default))

        def timing(key: str, default: float, where: str = where) -> float:
            return _num(setting(key, default), where, key)

        cards[cid] = Card(
            id=cid,
            type=ctype,
            data=ui,
            say_before=text["say_before"],
            confirm=text["confirm"],
            on_yes=text["on_yes"],
            on_no=text["on_no"] or text["on_no_or_timeout"],
            on_no_or_timeout=text["on_no_or_timeout"] or text["on_no"],
            auto_hide_s=auto_hide,
            confirm_after_s=timing("confirm_after_s", 6),
            retry_after_s=timing("retry_after_s", 10),
            mini_when_new_question=bool(setting("mini_when_new_question", True)),
            triggers=triggers,
            trigger_words=tuple(
                t.strip() for t in raw.get("triggers") or [] if isinstance(t, str) and t.strip()
            ),
            doc_sections=docs,
            show_with_lines=tuple(str(x) for x in raw.get("show_with_lines") or []),
            facts=facts,
            prompt_hint=fill(raw.get("prompt_hint")),
            placeholder=placeholder,
        )  # fmt: skip
    return CardCatalog(cards, unavailable, warnings, placeholder_url)


# -- the check-in loop (spec 2.2) -------------------------------------------------------


@dataclass(slots=True)
class Active:
    card: Card
    state: str  # intro | asking | waiting | resting | mini | static
    asks: int = 0
    next_at: float | None = None  # when to ask (again)
    hide_at: float | None = None


class CardStage:
    """The card on screen and its check-in timers. Returns events for the kiosk to send.

    Events are the UI's (PMC1): show_card, card_state, card_mini, hide_card, plus
    `say` {text, source} for a card line Ada says out loud (captions now, TTS later).
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.active: Active | None = None

    @property
    def current(self) -> str | None:
        return self.active.card.id if self.active else None

    @property
    def listening(self) -> bool:
        """A short typed "yes" / "not yet" right now answers the card, not the model."""
        a = self.active
        return (
            a is not None
            and a.card.checks_in
            and a.state in {"intro", "asking", "waiting", "resting"}
        )

    def _say(self, card: Card, text: str) -> list[dict[str, Any]]:
        return [{"type": "say", "text": text, "source": f"card:{card.id}"}] if text else []

    def show(self, card: Card, now: float, *, say_intro: bool) -> list[dict[str, Any]]:
        out = self.clear()
        a = Active(card, "intro" if card.checks_in else "static")
        a.hide_at = now + card.auto_hide_s if card.auto_hide_s > 0 else None
        if card.checks_in:
            a.next_at = now + card.confirm_after_s
        self.active = a
        out.append({"type": "show_card", "id": card.id, "data": card.ui_data(self.rng)})
        if card.checks_in:
            out.append({"type": "card_state", "id": card.id, "state": "intro"})
        if say_intro:
            out += self._say(card, card.say_before)
        return out

    def step(self, now: float) -> list[dict[str, Any]]:
        a = self.active
        if a is None:
            return []
        if a.hide_at is not None and now >= a.hide_at:  # timeout: hide quietly
            self.active = None
            return [{"type": "card_state", "id": a.card.id, "state": "timeout"},
                    {"type": "hide_card", "id": a.card.id}]  # fmt: skip
        if a.next_at is not None and now >= a.next_at and a.state in {"intro", "waiting"}:
            a.state, a.asks, a.next_at = "asking", a.asks + 1, None
            ask = {"type": "card_state", "id": a.card.id, "state": "asking", "text": a.card.confirm}
            return [ask, *self._say(a.card, a.card.confirm)]
        return []

    def reply(self, card_id: str | None, answer: str, now: float) -> list[dict[str, Any]]:
        a = self.active
        if a is None or not a.card.checks_in or (card_id and card_id != a.card.id):
            return []
        if a.state == "mini":
            return []
        if answer == "yes":
            self.active = None  # the UI shows "got it" and slides the card out itself
            return [{"type": "card_state", "id": a.card.id, "state": "yes", "text": a.card.on_yes},
                    *self._say(a.card, a.card.on_yes)]  # fmt: skip
        if answer != "no":
            return []
        if a.asks <= 1 and a.state != "resting":  # first "not yet": wait, then ask once more
            a.state, a.next_at = "waiting", now + a.card.retry_after_s
            text = a.card.on_no
            secs = a.card.retry_after_s
        else:  # asked twice already: stop asking, the card stays until it times out
            a.state, a.next_at = "resting", None
            text = a.card.on_no_or_timeout
            secs = max(1.0, (a.hide_at - now) if a.hide_at else a.card.retry_after_s)
        return [{"type": "card_state", "id": a.card.id, "state": "waiting", "text": text,
                 "seconds": round(secs, 1)},
                *self._say(a.card, text)]  # fmt: skip

    def new_question(self) -> list[dict[str, Any]]:
        """The visitor asked something else: keep the card small (or hide it), stop asking."""
        a = self.active
        if a is None or a.state == "mini":
            return []
        if not a.card.mini_when_new_question:
            return self.clear()
        a.state, a.next_at = "mini", None
        return [{"type": "card_mini", "id": a.card.id}]

    def clear(self) -> list[dict[str, Any]]:
        a, self.active = self.active, None
        return [{"type": "hide_card", "id": a.card.id}] if a else []
