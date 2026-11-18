# AI Lab Assistant (ada)

A local, in-character AI assistant for the SFSU AI Lab. It runs entirely on the lab's
NVIDIA GB10 through Ollama. No cloud APIs, no telemetry.

Project brief: [CLAUDE.md](CLAUDE.md) · status: [docs/STATUS.md](docs/STATUS.md) ·
decisions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Quick start (on the GB10)

```bash
make setup     # uv-managed Python 3.12 + deps
make health    # Ollama up? models present? each model answers?
make bench     # first-token latency + tokens/sec (thinking off, real system prompt)
make ingest    # index lab_docs/ for retrieval (re-run after editing any doc)
make chat      # talk to it in the terminal (/reset, /stats, /quit)
make chat-demo # scripted conversation + latency report
make rag-search Q="how do I join?"  # which doc sections a question retrieves
make rag-probe # score golden lab questions on the eval corpus (run make ingest-fixtures first)
make rag-check # does the right doc come back for each test question? (evals/retrieval.yaml)
make chat-lab  # 10 real lab questions through the full pipeline
make kill-on REASON="demo"   # out-of-order mode, instantly (make kill-off / kill-status)
make admin     # localhost admin endpoint for staff tools (needs ADA_ADMIN_TOKEN)
make m3-probe  # attack prompts through both guards, shows every guard decision
make m3-latency  # first-sentence time with both guards on
make kiosk     # the lab screen: http://127.0.0.1:8080/ui/ (websocket /ws, health /healthz)
make kiosk-browser  # open it full screen in Chromium kiosk mode (Alt+F4 to leave)
make test      # unit tests (Ollama mocked)
make test-gb10 # live tests against local Ollama
```

Models and settings live in `config.yaml`. Override any key with `ADA_<KEY>`:

```bash
ADA_CHAT_MODEL=gemma4:12b make bench
```

## The kiosk screen (M5)

`make kiosk` serves the screen on `127.0.0.1:8080` (`kiosk_host` / `kiosk_port` in
config.yaml) and runs every message through the same guarded pipeline as `make chat`.
`make kiosk-browser` (in another terminal, on the GB10's desktop) waits for the server and
opens Chromium in kiosk mode with a throwaway incognito profile. Leave with Alt+F4.

- UI (M5a, light theme): `ui/index.html`, `kiosk.css`, `kiosk.js` (events -> states,
  captions), `blob.js` (Three.js smooth shaded blob, bundled in `ui/vendor/`); spec
  `docs/ui-spec.md` v3; copy in `content/ui_strings.yaml` (designer-owned). Reload the page
  to pick up copy edits.
- Idle acts: `content/idle_acts.yaml`, picked by `app/idle.py`, visual only, every 45-120 s
  while nobody is there; any tap or typing stops them.
- Talking: tap the blob or the button (until M6 voice, a tap opens the typing box). Staff:
  `` ` `` opens/closes the typing box; Esc says goodbye, Esc twice resets at once.
- Debug: `http://127.0.0.1:8080/ui/?debug=1` shows a panel that forces every blob state and
  idle act (visual only) and shows fps.
- Kill switch: `make kill-on` / `make kill-off` (or `POST /admin/kill-switch` with
  `ADA_ADMIN_TOKEN`) flips the live screen to "out of order" within ~0.5 s.
- Start on boot (not enabled): `scripts/kiosk-autostart.sh install|uninstall|status`.

## Layout

```
app/       Python package (config, Ollama client, prompt, lines, sessions, pipeline, chat CLI,
           rag/, guards/, evals/, server.py = kiosk)
prompts/   system prompt templates (designer)
content/   persona bible, lines (designer)
lab_docs/  lab knowledge base (Markdown)
evals/     eval sets + bench prompts
ui/        kiosk web UI (+ mockups/, fonts/)
scripts/   kiosk browser launcher, autostart installer
deploy/    systemd + autostart templates (not enabled)
docs/      STATUS, DECISIONS, specs
tests/     pytest
```
