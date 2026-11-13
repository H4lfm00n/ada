"""Phone numbers, emails, and links: finding them, and the allowlist built from the docs.

The output guard blocks any contact the model writes unless the lab docs (or the
designer's canned lines) contain it, so the kiosk can give University Police's number or
ai@sfsu.edu but never an invented one. The input guard uses the same finder to spot a
visitor typing their own details on the public screen.

The allowlist is built at `make ingest` from the same parse the index uses (TODO sections
left out) and saved next to the index as `<index>.contacts.json`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Kind = Literal["phone", "email", "url", "domain"]

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
TLDS = "edu|com|org|net|gov|io|ai|dev|app|co|us|gg|me|ly|info|tv|xyz|ca|uk|de|so|sh|link|page"
DOMAIN_RE = re.compile(
    rf"(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{TLDS})\b(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
PHONE_RES = (
    # (415) 338-2222, 415-338-2222, 415.338.2222, +1 415 338 2222
    re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"),
    re.compile(r"(?<!\d)\d{10}(?!\d)"),  # 4153382222
    re.compile(r"(?<![\d-])\d{3}[.-]\d{4}(?![\d-])"),  # 338-2222 (campus short form)
)
_TRAILING = ".,;:!?)]}'\""


@dataclass(frozen=True, slots=True)
class Contact:
    kind: Kind
    raw: str  # as written
    key: str  # normalized form used for allowlist lookups


def _digits(s: str) -> str:
    d = re.sub(r"\D", "", s)
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def _norm_url(s: str) -> str:
    s = s.lower().rstrip(_TRAILING)
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


def _host(url_key: str) -> str:
    return url_key.split("/", 1)[0]


def find_contacts(text: str) -> list[Contact]:
    """Every phone number, email, URL, and bare domain in `text`, in no particular order."""
    found: list[Contact] = []
    rest = text
    for m in EMAIL_RE.finditer(text):
        raw = m.group(0).rstrip(_TRAILING)
        found.append(Contact("email", raw, raw.lower()))
    rest = EMAIL_RE.sub(" ", rest)
    for m in URL_RE.finditer(rest):
        raw = m.group(0).rstrip(_TRAILING)
        found.append(Contact("url", raw, _norm_url(raw)))
    rest = URL_RE.sub(" ", rest)
    for m in DOMAIN_RE.finditer(rest):
        raw = m.group(0).rstrip(_TRAILING)
        key = _norm_url(raw)
        found.append(Contact("url" if "/" in key else "domain", raw, key))
    rest = DOMAIN_RE.sub(" ", rest)
    for rx in PHONE_RES:
        for m in rx.finditer(rest):
            found.append(Contact("phone", m.group(0), _digits(m.group(0))))
        rest = rx.sub(" ", rest)
    return found


@dataclass(slots=True)
class ContactAllowlist:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)  # 10 (or 7) digits
    urls: set[str] = field(default_factory=set)  # normalized, with path
    hosts: set[str] = field(default_factory=set)

    @classmethod
    def from_texts(cls, texts: list[str]) -> ContactAllowlist:
        al = cls()
        for t in texts:
            al.add_text(t)
        return al

    def add_text(self, text: str) -> None:
        for c in find_contacts(text):
            if c.kind == "email":
                self.emails.add(c.key)
                self.hosts.add(c.key.split("@", 1)[1])
            elif c.kind == "phone":
                self.phones.add(c.key)
            else:
                self.urls.add(c.key)
                self.hosts.add(_host(c.key))

    def allows(self, c: Contact) -> bool:
        if c.kind == "email":
            return c.key in self.emails
        if c.kind == "phone":
            if c.key in self.phones:
                return True
            # A 7-digit campus short form is fine if it's the tail of a listed number.
            return len(c.key) == 7 and any(p.endswith(c.key) for p in self.phones)
        if c.kind == "domain" or "/" not in c.key:  # "sfsu.edu" or "https://sfsu.edu"
            return c.key in self.hosts
        return c.key in self.urls  # a URL with a path must match exactly

    def blocked(self, text: str) -> list[Contact]:
        return [c for c in find_contacts(text) if not self.allows(c)]

    @property
    def size(self) -> int:
        return len(self.emails) + len(self.phones) + len(self.urls) + len(self.hosts)

    # -- persistence (written by `make ingest`) -----------------------------------------

    def to_json(self) -> dict[str, list[str]]:
        return {
            "emails": sorted(self.emails),
            "phones": sorted(self.phones),
            "urls": sorted(self.urls),
            "hosts": sorted(self.hosts),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> ContactAllowlist:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            emails=set(data.get("emails", [])),
            phones=set(data.get("phones", [])),
            urls=set(data.get("urls", [])),
            hosts=set(data.get("hosts", [])),
        )

    def merged(self, other: ContactAllowlist) -> ContactAllowlist:
        return ContactAllowlist(
            self.emails | other.emails,
            self.phones | other.phones,
            self.urls | other.urls,
            self.hosts | other.hosts,
        )
