# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
./run.sh
```

Sources `.env` if present, creates `.venv`, installs dependencies, starts FastAPI on `http://localhost:8001` with auto-reload.

**Docker (preferred for persistent deployment):**
```bash
docker compose up -d
docker compose up -d --build   # after code changes
```

## AI Provider Configuration

Set in `.env`. Auto-detects from available keys in priority order: Anthropic → OpenAI → Gemini.

```bash
# Pick one provider
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...

# Override auto-detection
AI_PROVIDER=gemini          # anthropic | openai | gemini

# Override the model used by a provider
AI_MODEL=gemini-2.5-flash   # default per provider: claude-sonnet-4-6 / gpt-4o-mini / gemini-2.0-flash
```

Without any key, the app works normally — auto-scheduler handles task selection and AI features are hidden.

## Architecture

FastAPI + HTMX + Jinja2 app backed by SQLite. No JS build step, no external CSS framework.

**Data hierarchy:**
```
Goal → Project → Task → Session
```
Cascading deletes enforced at the DB level via foreign keys (`PRAGMA foreign_keys=ON`).

**Key files:**
- `main.py` — All HTTP route handlers
- `database.py` — SQLite schema, `db_context()` connection manager, all queries. `DB_PATH` env var overrides default path (used in Docker to point at `/data/`)
- `models.py` — Dataclasses for Goal, Project, Task, Session
- `scheduler.py` — Auto-schedules high-priority tasks if fewer than 3 are queued for today (fallback when AI is disabled)
- `ai.py` — Multi-provider AI integration. `_call_ai()` dispatches to Anthropic SDK or OpenAI SDK (also used for Gemini via its OpenAI-compatible endpoint). All AI functions fail gracefully and return `None` on error.

**Focus mode:** Starting any task via `POST /tasks/{id}/start` ends any other active session, creates a new one, and returns `HX-Redirect: /`. The dashboard detects an active session and renders the standalone `focus.html` instead of the normal dashboard.

**AI life coach & daily briefing:** On first dashboard load each day, `ai.generate_daily_briefing()` is called with the full hierarchy plus `performance` stats (completion rate, streak, neglected goals, yesterday's results). The AI returns structured JSON with `greeting`, `coaching_note`, `motivation`, and a `schedule` of task IDs with `time_block` (morning/midday/afternoon/evening) and `reason`. Tasks are grouped by time block on the dashboard. Cached in `briefings` table; `POST /briefing/regenerate` clears cache.

**Week planning (`/week`):** Shows a 7-day grid (Mon–Sun). `POST /week/plan` calls `ai.generate_week_plan()` which distributes open tasks across the week with per-day themes. Plan stored in `week_plans` table. Tasks are scheduled to their assigned dates. Responsive: collapses to single column on mobile.

**Performance tracking:** `database.fetch_performance_stats()` computes completion rate (14-day window), streak (consecutive days with completions), yesterday's results, and neglected goals (no activity in 7+ days). Used by both daily briefing and week planning for context-aware coaching.

**Goal roadmap (`/goals/{id}/plan`):** Calls `ai.generate_goal_roadmap()` synchronously on page load (~5–10s). Returns a phased plan (phases → projects → tasks). The template embeds the full plan as a JSON script tag; JS collects checkbox selections and POSTs them as `selected_json` to `POST /goals/{id}/apply-plan`.

**Database:** SQLite at `life_manager.db` (or `$DB_PATH`), WAL mode, auto-initialized on startup via `db.init_db()`. Tables: goals, projects, tasks, sessions, briefings, week_plans.
