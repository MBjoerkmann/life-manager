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
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...

AI_PROVIDER=gemini          # anthropic | openai | gemini (override auto-detection)
AI_MODEL=gemini-2.5-flash   # default per provider: claude-sonnet-4-6 / gpt-4o-mini / gemini-2.0-flash
```

Without any key, the app works normally — scheduler handles task selection and AI features are hidden.

## Architecture

FastAPI + HTMX + Jinja2 backed by SQLite. No JS build step, no external CSS framework.

**Data hierarchy:**
```
Goal → Project → Task → Subtask (Step) → Session
```
Cascading deletes enforced at DB level (`PRAGMA foreign_keys=ON`).

**Key files:**
- `main.py` — All HTTP route handlers (~900+ lines)
- `database.py` — SQLite schema, `db_context()` connection manager, all queries. `DB_PATH` env var overrides default path (used in Docker to point at `/data/`)
- `models.py` — Dataclasses for Goal, Project, Task, Session
- `scheduler.py` — Silently rolls past unfinished tasks forward to today; auto-schedules high-priority tasks if fewer than 3 are queued
- `ai.py` — Multi-provider AI. `_call_ai()` dispatches to Anthropic SDK or OpenAI SDK (Gemini uses OpenAI-compatible endpoint). All AI fails gracefully, returns `None` on error.

**Design principles:**
- No "overdue" concept — past unscheduled tasks roll forward silently
- All AI is strictly user-triggered (never on page load)
- Focus on small victories and accumulating output over time

## Pages & Routes

| Page | Route | Notes |
|------|-------|-------|
| Dashboard | `GET /` | Shows active focus session or daily task view |
| Focus mode | redirected from `POST /tasks/{id}/start` | Full-screen timer via `focus.html` |
| Week | `GET /week` | 7-day grid, manual scheduling + AI week plan |
| Goals | `GET /goals` | Expandable cards: goal → project → task inline |
| Projects | `GET /projects` | Expandable cards: project → task inline |
| Project detail | `GET /projects/{id}` | Task list with add-task form |
| Task plan | `GET /tasks/{id}/plan` | Subtask list, AI breakdown, timer, notes |
| Tasks | `GET /tasks` | Filtered task list |
| Journal | `GET /journal` | Sessions grouped by day, time-range filter |
| Review | `GET /review` | Stats + "Generate Review" button (AI on demand) |
| Goal roadmap | `GET /goals/{id}/plan` | "Generate Roadmap" button → phased AI plan |

**Markdown exports:** `/tasks/{id}/export.md`, `/projects/{id}/export.md`, `/goals/{id}/export.md`, `/journal/export.md`

## Key Behaviours

**Focus mode:** `POST /tasks/{id}/start` ends any active session, creates a new one, returns `HX-Redirect: /`. Dashboard detects active session and renders `focus.html` instead.

**AI daily briefing:** User-triggered via button on dashboard. `POST /briefing/generate` calls `ai.generate_daily_briefing()` with full hierarchy + performance stats. Returns JSON with `greeting`, `coaching_note`, `motivation`, and `schedule` (task IDs with `time_block`: morning/midday/afternoon/evening). Cached in `briefings` table.

**Week planning:** `POST /week/plan` calls `ai.generate_week_plan()` which distributes open tasks across the week. Stored in `week_plans` table; tasks scheduled to assigned dates.

**Performance tracking:** `database.fetch_performance_stats()` — completion rate (14-day window), streak (consecutive days), yesterday's results, neglected goals (no activity 7+ days).

**Goal roadmap:** `POST /goals/{id}/plan` calls `ai.generate_goal_roadmap()`. Returns phased plan (phases → projects → tasks). Template embeds plan as JSON script tag; JS POSTs checkbox selections as `selected_json` to `POST /goals/{id}/apply-plan`.

**Subtasks (Steps):** Each task has subtasks with title, description (AI guidance), notes (auto-saved via HTMX), and status. `partials/subtask_item.html` is a `<details>` element with expanded notes/action area. AI breakdown via `POST /tasks/{id}/subtasks/generate`; templates via `POST /tasks/{id}/subtasks/from-template/{tpl_id}`. 6 seed templates in DB.

**Expandable cards:** Goals and Projects pages use nested `<details>` elements — goal expands to show projects, each project expands to show tasks with status icons and links to task plan pages.

**Database:** SQLite at `life_manager.db` (or `$DB_PATH`), WAL mode, auto-initialized on startup via `db.init_db()`. Tables: `goals`, `projects`, `tasks`, `subtasks`, `subtask_templates`, `sessions`, `briefings`, `week_plans`.
