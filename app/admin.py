"""Staff controls: kill switch (CLI + localhost HTTP endpoint) and transcript pruning.

CLI (no server needed, works while `make chat` / the kiosk is running):
  python -m app.admin status
  python -m app.admin on  --reason "hallway demo" --by atharva
  python -m app.admin off --by atharva
  python -m app.admin prune-transcripts
  python -m app.admin serve                 # the HTTP endpoint below

HTTP (127.0.0.1 only; every call needs `Authorization: Bearer $ADA_ADMIN_TOKEN`):
  GET  /admin/health        -> {"ok": true, "kill_switch": {...}}
  GET  /admin/kill-switch   -> {"on": false, "source": "default", ...}
  POST /admin/kill-switch   {"on": true, "reason": "...", "by": "..."}

The M5.5 Slack bot runs on this machine and calls the POST with the token; the M5 kiosk
server mounts `router` instead of running a second process. With no ADA_ADMIN_TOKEN set
the endpoint refuses every request (503): there is no "open" mode.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.killswitch import KillSwitch

TOKEN_ENV = "ADA_ADMIN_TOKEN"
MIN_TOKEN_CHARS = 16


class KillRequest(BaseModel):
    on: bool
    reason: str = Field("", max_length=200)
    by: str = Field("", max_length=64)


def make_router(kill: KillSwitch, token: str | None) -> APIRouter:
    def auth(authorization: Annotated[str | None, Header()] = None) -> None:
        if not token or len(token) < MIN_TOKEN_CHARS:
            raise HTTPException(
                503, f"admin disabled: set {TOKEN_ENV} (>= {MIN_TOKEN_CHARS} chars)"
            )
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "bad or missing token")

    router = APIRouter(prefix="/admin", dependencies=[Depends(auth)])

    @router.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "kill_switch": kill.state().to_dict()}

    @router.get("/kill-switch")
    def get_kill() -> dict[str, object]:
        return kill.state().to_dict()

    @router.post("/kill-switch")
    def set_kill(req: KillRequest) -> dict[str, object]:
        state = kill.set(req.on, req.reason, req.by or "admin-api")
        if state.on != req.on:  # e.g. switched off in the file, but config forces it on
            raise HTTPException(409, {"wanted": req.on, "state": state.to_dict()})
        return state.to_dict()

    return router


def make_app(settings: Settings, token: str | None = None) -> FastAPI:
    app = FastAPI(title="AI Lab Assistant admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(make_router(KillSwitch.from_settings(settings), token))
    return app


def _print(state: dict[str, object]) -> None:
    print(json.dumps(state))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show the kill switch state")
    for name in ("on", "off"):
        p = sub.add_parser(name, help=f"kill switch {name}")
        p.add_argument("--reason", default="")
        p.add_argument("--by", default=os.environ.get("USER", "cli"))
    sub.add_parser("prune-transcripts", help="delete transcripts past the retention period")
    sub.add_parser("serve", help="run the admin HTTP endpoint on admin_host:admin_port")
    args = parser.parse_args(argv)

    settings = load_settings()
    kill = KillSwitch.from_settings(settings)
    if args.cmd == "status":
        _print(kill.state().to_dict())
        return 0
    if args.cmd in ("on", "off"):
        state = kill.set(args.cmd == "on", args.reason, args.by)
        _print(state.to_dict())
        if args.cmd == "off" and state.on:
            print(f"still ON: {state.reason}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "prune-transcripts":
        from app.transcripts import TranscriptLogger

        logger = TranscriptLogger(
            settings.content_path(settings.transcripts_dir), settings.transcript_retention_days
        )
        print(f"removed {logger.prune()} day folder(s) older than "
              f"{settings.transcript_retention_days} days")  # fmt: skip
        return 0

    # serve
    import uvicorn

    os.environ.setdefault("OTEL_SDK_DISABLED", "true")  # belt and braces: no telemetry
    token = os.environ.get(TOKEN_ENV)
    if not token or len(token) < MIN_TOKEN_CHARS:
        print(f"set {TOKEN_ENV} (>= {MIN_TOKEN_CHARS} chars) first", file=sys.stderr)
        return 2
    uvicorn.run(make_app(settings, token), host=settings.admin_host, port=settings.admin_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
