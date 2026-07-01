# GTD

A personal "Getting Things Done" task manager. Python + FastAPI backend with server-rendered Jinja2 HTML pages, a SQLite store, a CLI (`bin/gtd`), an LLM-powered "clarify" engine (via LiteLLM), and an optional Feishu (Lark) chat bot for capturing items.

## Cursor Cloud specific instructions

Dependencies are installed into a virtualenv at `.venv` by the startup update script. Use the venv interpreters directly (no need to activate): `.venv/bin/python`, `.venv/bin/uvicorn`, `.venv/bin/pytest`.

### Services

- **Web app (single service).** Run the dev server with hot reload:
  `.venv/bin/uvicorn gtd.main:app --reload --host 127.0.0.1 --port 8420`
  Pages: `/inbox`, `/actions`, `/projects`, `/waiting`, `/someday`, `/reference`, `/done`. JSON API is under `/api/*`. Port `8420` is the app default (see `gtd/settings.py`).
- **CLI (same codebase, no server needed):** `.venv/bin/python bin/gtd <cmd>` (e.g. `add`, `inbox`, `clarify <id>`, `confirm <id>`, `actions`, `recommend`). Both the CLI and web app share the SQLite DB.

### Config / non-obvious gotchas

- **LLM defaults to `mock`.** With no `.env`, `settings.llm_model` is `"mock"` (keyword heuristic, no API key, fully offline) — ideal for local dev/testing. Do NOT blindly `cp .env.example .env`: that example sets a real model (`anthropic/...`) plus placeholder Feishu creds, which makes the app attempt real LLM/Feishu network calls on startup. To use a real LLM, set `LLM_MODEL=provider/model` and `LLM_API_KEY` in `.env`.
- **Feishu bot is opt-in.** `start_feishu()` is a no-op unless both `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are set, so the server starts cleanly without them.
- **SQLite DB** lives at `data/gtd.db` (auto-created, WAL mode, gitignored). Delete the `data/` dir to reset all state, then restart the server.
- **No linter is configured** in this repo. Tests run with `.venv/bin/pytest` (see `tests/`).
- The clarify **confirm** step (`/api/clarify/{id}/confirm`) creates the target entity but marks the inbox item `clarified` without archiving it; the manual **route** step (`/api/inbox/{id}/route`) both creates the entity and archives the inbox item.
