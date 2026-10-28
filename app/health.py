"""`make health`: is Ollama up, are all configured models present and working?

Usage: python -m app.health [--quick]
  --quick  only check the server and model tags (no model loading)
Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass

from app.config import Settings, load_settings
from app.interfaces import Message
from app.ollama_client import OllamaClient, OllamaError, normalize_tag

# Capability each model role must advertise via /api/show (Ollama >= 0.7 reports these).
REQUIRED_CAPABILITY = {"chat": "completion", "guard": "completion", "embed": "embedding"}


@dataclass(slots=True)
class Check:
    name: str
    status: str  # PASS | FAIL | INFO
    detail: str = ""

    def __str__(self) -> str:
        return f"[{self.status}] {self.name}" + (f": {self.detail}" if self.detail else "")


async def run_health(settings: Settings, client: OllamaClient, quick: bool = False) -> list[Check]:
    checks: list[Check] = []

    try:
        version = await client.version()
        installed = {normalize_tag(t) for t in await client.list_models()}
    except OllamaError as exc:
        return [Check("ollama server", "FAIL", str(exc))]
    checks.append(Check("ollama server", "PASS", f"{settings.ollama_url} (v{version})"))

    present: dict[str, bool] = {}
    for role, tag in settings.model_tags.items():
        ok = normalize_tag(tag) in installed
        present[role] = ok
        if not ok:
            checks.append(
                Check(f"{role} model", "FAIL", f"{tag} not found; run: ollama pull {tag}")
            )
            continue
        try:
            info = await client.show(tag)
        except OllamaError as exc:
            checks.append(Check(f"{role} model", "FAIL", f"{tag}: {exc}"))
            present[role] = False
            continue
        caps = info.capabilities
        need = REQUIRED_CAPABILITY[role]
        if caps and need not in caps:
            checks.append(
                Check(f"{role} model", "FAIL", f"{tag} lacks '{need}' capability (has {caps})")
            )
            present[role] = False
            continue
        size = info.details.get("parameter_size", "?")
        quant = info.details.get("quantization_level", "?")
        checks.append(Check(f"{role} model", "PASS", f"{tag} ({size}, {quant})"))
        if role == "chat":
            checks.append(
                Check(
                    "chat capabilities",
                    "INFO",
                    f"{', '.join(caps) or 'not reported'}"
                    f" | tools={'yes' if 'tools' in caps else 'no'}"
                    f" | thinking={'yes' if 'thinking' in caps else 'no'}",
                )
            )

    if quick:
        return checks

    if present.get("chat"):
        checks.append(await _smoke_chat(settings, client))
    if present.get("guard"):
        checks.append(await _smoke_guard(settings, client))
    if present.get("embed"):
        checks.append(await _smoke_embed(settings, client))
    return checks


async def _smoke_chat(settings: Settings, client: OllamaClient) -> Check:
    name = "chat smoke (think=false)"
    content, thinking = "", ""
    t0 = time.perf_counter()
    try:
        async for d in client.chat_stream(
            settings.chat_model,
            [Message("user", "Reply with exactly: ready")],
            think=False,
            options={"num_predict": 16, "temperature": 0},
            keep_alive=settings.keep_alive,
        ):
            content += d.content
            thinking += d.thinking
    except OllamaError as exc:
        return Check(name, "FAIL", str(exc))
    elapsed = time.perf_counter() - t0
    if thinking or "<think>" in content:
        return Check(name, "FAIL", f"thinking leaked ({len(thinking)} chars); reply={content!r}")
    if not content.strip():
        return Check(name, "FAIL", "empty reply")
    return Check(name, "PASS", f"reply={content.strip()[:60]!r} in {elapsed:.1f}s (incl. load)")


async def _smoke_guard(settings: Settings, client: OllamaClient) -> Check:
    name = "guard smoke"
    out = ""
    try:
        async for d in client.chat_stream(
            settings.guard_model,
            [Message("user", "Hi! What does the AI Lab do?")],
            options={"num_predict": 10, "temperature": 0},
            keep_alive=settings.keep_alive,
        ):
            out += d.content
    except OllamaError as exc:
        return Check(name, "FAIL", str(exc))
    verdict = out.strip().split()[0].lower() if out.strip() else ""
    if verdict != "safe":
        return Check(name, "FAIL", f"benign prompt classified as {out.strip()!r}")
    return Check(name, "PASS", "benign prompt -> safe")


async def _smoke_embed(settings: Settings, client: OllamaClient) -> Check:
    name = "embed smoke"
    try:
        vecs = await client.embed(settings.embed_model, ["hello lab"], settings.keep_alive)
    except OllamaError as exc:
        return Check(name, "FAIL", str(exc))
    if not vecs or not vecs[0]:
        return Check(name, "FAIL", "no embedding returned")
    return Check(name, "PASS", f"dim={len(vecs[0])}")


async def _amain(quick: bool) -> int:
    settings = load_settings()
    print(
        f"bot={settings.bot_name}  chat={settings.chat_model}  guard={settings.guard_model}"
        f"  embed={settings.embed_model}"
    )
    async with OllamaClient.from_settings(settings) as client:
        checks = await run_health(settings, client, quick=quick)
    for c in checks:
        print(c)
    failed = [c for c in checks if c.status == "FAIL"]
    print(f"\nHEALTH: {'FAIL (' + str(len(failed)) + ')' if failed else 'OK'}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="skip model smoke calls")
    args = parser.parse_args()
    sys.exit(asyncio.run(_amain(args.quick)))


if __name__ == "__main__":
    main()
