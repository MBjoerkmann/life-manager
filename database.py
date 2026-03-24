import sqlite3
from contextlib import contextmanager
from pathlib import Path

import os

_default_db = Path(__file__).parent / "life_manager.db"
DB_PATH = Path(os.environ.get("DB_PATH", str(_default_db)))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_context():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_context() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT,
                priority    INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                active      INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id     INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                description TEXT,
                deadline    TEXT,
                priority    INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title             TEXT NOT NULL,
                description       TEXT,
                estimated_minutes INTEGER NOT NULL DEFAULT 45,
                status            TEXT NOT NULL DEFAULT 'todo'
                                  CHECK(status IN ('todo','in_progress','done','skipped')),
                scheduled_date    TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at   TEXT,
                notes      TEXT
            );

            CREATE TABLE IF NOT EXISTS briefings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                message    TEXT NOT NULL,
                tasks_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS week_plans (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL UNIQUE,
                overview   TEXT NOT NULL,
                plan_json  TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS subtasks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id           INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                title             TEXT NOT NULL,
                description       TEXT,
                notes             TEXT DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'todo'
                                  CHECK(status IN ('todo','in_progress','done','skipped')),
                sort_order        INTEGER NOT NULL DEFAULT 0,
                estimated_minutes INTEGER NOT NULL DEFAULT 15,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS subtask_templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                steps_json  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
    # Migrate: add notes column if missing (for existing DBs)
    with db_context() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(subtasks)").fetchall()]
        if "notes" not in cols:
            conn.execute("ALTER TABLE subtasks ADD COLUMN notes TEXT DEFAULT ''")
    with db_context() as conn:
        _seed_default_templates(conn)


# ---------------------------------------------------------------------------
# Goal helpers
# ---------------------------------------------------------------------------

def fetch_all_goals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM goals ORDER BY priority DESC, created_at ASC"
    ).fetchall()


def fetch_goal(conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()


def insert_goal(conn: sqlite3.Connection, title: str, description: str, priority: int) -> int:
    cur = conn.execute(
        "INSERT INTO goals (title, description, priority) VALUES (?, ?, ?)",
        (title, description, priority),
    )
    return cur.lastrowid


def delete_goal(conn: sqlite3.Connection, goal_id: int):
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------

def fetch_projects_for_goal(conn: sqlite3.Connection, goal_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM projects WHERE goal_id = ? ORDER BY priority DESC, created_at ASC",
        (goal_id,),
    ).fetchall()


def fetch_all_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM projects ORDER BY priority DESC, created_at ASC"
    ).fetchall()


def fetch_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def insert_project(
    conn: sqlite3.Connection,
    goal_id: int,
    title: str,
    description: str,
    deadline: str | None,
    priority: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO projects (goal_id, title, description, deadline, priority)
           VALUES (?, ?, ?, ?, ?)""",
        (goal_id, title, description, deadline or None, priority),
    )
    return cur.lastrowid


def delete_project(conn: sqlite3.Connection, project_id: int):
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def fetch_tasks_for_project(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at ASC",
        (project_id,),
    ).fetchall()


def fetch_all_tasks(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()


def fetch_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def insert_task(
    conn: sqlite3.Connection,
    project_id: int,
    title: str,
    description: str,
    estimated_minutes: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO tasks (project_id, title, description, estimated_minutes)
           VALUES (?, ?, ?, ?)""",
        (project_id, title, description, estimated_minutes),
    )
    return cur.lastrowid


def update_task_status(conn: sqlite3.Connection, task_id: int, status: str):
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def update_task(
    conn: sqlite3.Connection,
    task_id: int,
    title: str,
    description: str,
    estimated_minutes: int,
):
    conn.execute(
        "UPDATE tasks SET title = ?, description = ?, estimated_minutes = ? WHERE id = ?",
        (title, description, estimated_minutes, task_id),
    )


def delete_task(conn: sqlite3.Connection, task_id: int):
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def schedule_task(conn: sqlite3.Connection, task_id: int, date_str: str):
    conn.execute(
        "UPDATE tasks SET scheduled_date = ? WHERE id = ?", (date_str, task_id)
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def insert_session(conn: sqlite3.Connection, task_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (task_id) VALUES (?)", (task_id,)
    )
    return cur.lastrowid


def fetch_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def end_session(conn: sqlite3.Connection, session_id: int, notes: str | None):
    conn.execute(
        "UPDATE sessions SET ended_at = datetime('now'), notes = ? WHERE id = ?",
        (notes, session_id),
    )


def fetch_active_session_for_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sessions WHERE task_id = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def fetch_active_session_any(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Get the current active session across all tasks, with full context."""
    return conn.execute(
        """SELECT s.id, s.task_id, s.started_at, s.ended_at, s.notes,
                  t.title AS task_title, t.description AS task_description,
                  t.estimated_minutes, t.project_id,
                  p.title AS project_title,
                  g.title AS goal_title
           FROM sessions s
           JOIN tasks t ON t.id = s.task_id
           JOIN projects p ON p.id = t.project_id
           JOIN goals g ON g.id = p.goal_id
           WHERE s.ended_at IS NULL
           ORDER BY s.started_at DESC LIMIT 1"""
    ).fetchone()


def count_sessions_this_week(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM sessions
           WHERE ended_at IS NOT NULL
             AND date(ended_at) >= date('now', 'weekday 0', '-7 days')"""
    ).fetchone()
    return row["cnt"] if row else 0


def count_tasks_done_this_week(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM tasks
           WHERE status = 'done'
             AND date(created_at) >= date('now', 'weekday 0', '-7 days')"""
    ).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Briefing helpers
# ---------------------------------------------------------------------------

def fetch_briefing_for_date(conn: sqlite3.Connection, date_str: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM briefings WHERE date = ?", (date_str,)
    ).fetchone()


def insert_briefing(conn: sqlite3.Connection, date_str: str, message: str, tasks_json: str) -> int:
    cur = conn.execute(
        "INSERT INTO briefings (date, message, tasks_json) VALUES (?, ?, ?)",
        (date_str, message, tasks_json),
    )
    return cur.lastrowid


def delete_briefing_for_date(conn: sqlite3.Connection, date_str: str):
    conn.execute("DELETE FROM briefings WHERE date = ?", (date_str,))


# ---------------------------------------------------------------------------
# AI context helpers
# ---------------------------------------------------------------------------

def fetch_full_hierarchy(conn: sqlite3.Connection) -> list[dict]:
    """Full goal > project > open-task tree for AI context."""
    goals = fetch_all_goals(conn)
    result = []
    for g in goals:
        if not g["active"]:
            continue
        projects = fetch_projects_for_goal(conn, g["id"])
        proj_list = []
        for p in projects:
            if not p["active"]:
                continue
            tasks = conn.execute(
                """SELECT * FROM tasks
                   WHERE project_id = ? AND status IN ('todo', 'in_progress')
                   ORDER BY created_at ASC""",
                (p["id"],),
            ).fetchall()
            proj_list.append({"project": dict(p), "tasks": [dict(t) for t in tasks]})
        result.append({"goal": dict(g), "projects": proj_list})
    return result


def fetch_recent_completions(conn: sqlite3.Connection, days: int = 7) -> list[sqlite3.Row]:
    """Tasks completed recently (via sessions ended in the last N days)."""
    return conn.execute(
        """SELECT DISTINCT t.title, p.title AS project_title, g.title AS goal_title
           FROM tasks t
           JOIN projects p ON p.id = t.project_id
           JOIN goals g ON g.id = p.goal_id
           WHERE t.status = 'done'
           ORDER BY t.created_at DESC
           LIMIT 30"""
    ).fetchall()


def fetch_performance_stats(conn: sqlite3.Connection) -> dict:
    """Compute coaching-relevant performance metrics."""
    from datetime import date as _date, timedelta

    # Completion rate (last 14 days of scheduled tasks)
    row = conn.execute(
        """SELECT
               COUNT(CASE WHEN status = 'done' THEN 1 END) AS done,
               COUNT(CASE WHEN status IN ('done', 'skipped') THEN 1 END) AS resolved
           FROM tasks
           WHERE scheduled_date >= date('now', '-14 days')
             AND scheduled_date <= date('now')"""
    ).fetchone()
    done_14 = row["done"]
    resolved_14 = row["resolved"]
    completion_rate = round(done_14 / resolved_14 * 100) if resolved_14 > 0 else 0

    # Yesterday
    yrow = conn.execute(
        """SELECT
               COUNT(CASE WHEN status = 'done' THEN 1 END) AS done,
               COUNT(*) AS total
           FROM tasks
           WHERE scheduled_date = date('now', '-1 day')"""
    ).fetchone()

    # Streak: consecutive days (backward from today/yesterday) with a completed session
    streak_rows = conn.execute(
        """SELECT DISTINCT date(ended_at) AS d
           FROM sessions
           WHERE ended_at IS NOT NULL
           ORDER BY d DESC"""
    ).fetchall()
    streak = 0
    today = _date.today()
    expected = today
    for sr in streak_rows:
        d = _date.fromisoformat(sr["d"])
        if d == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif streak == 0 and d == expected - timedelta(days=1):
            expected = d
            streak = 1
            expected -= timedelta(days=1)
        else:
            break

    # Neglected goals: active goals with no completed task in last 7 days
    neglected = conn.execute(
        """SELECT g.title
           FROM goals g
           WHERE g.active = 1
             AND NOT EXISTS (
                 SELECT 1
                 FROM projects p
                 JOIN tasks t ON t.project_id = p.id
                 JOIN sessions s ON s.task_id = t.id
                 WHERE p.goal_id = g.id
                   AND t.status = 'done'
                   AND s.ended_at IS NOT NULL
                   AND date(s.ended_at) >= date('now', '-7 days')
             )"""
    ).fetchall()

    return {
        "completion_rate": completion_rate,
        "done_14d": done_14,
        "resolved_14d": resolved_14,
        "yesterday_done": yrow["done"] if yrow else 0,
        "yesterday_total": yrow["total"] if yrow else 0,
        "streak": streak,
        "neglected_goals": [r["title"] for r in neglected],
    }


def fetch_sessions_summary_this_week(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS count,
                  COALESCE(SUM(
                      CAST((julianday(ended_at) - julianday(started_at)) * 24 * 60 AS INTEGER)
                  ), 0) AS total_minutes
           FROM sessions
           WHERE ended_at IS NOT NULL
             AND date(ended_at) >= date('now', 'weekday 0', '-7 days')"""
    ).fetchone()
    return {"count": row["count"], "total_minutes": row["total_minutes"]}


# ---------------------------------------------------------------------------
# Week plan helpers
# ---------------------------------------------------------------------------

def fetch_week_plan(conn: sqlite3.Connection, week_start: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM week_plans WHERE week_start = ?", (week_start,)
    ).fetchone()


def insert_week_plan(conn: sqlite3.Connection, week_start: str, overview: str, plan_json: str) -> int:
    cur = conn.execute(
        "INSERT OR REPLACE INTO week_plans (week_start, overview, plan_json) VALUES (?, ?, ?)",
        (week_start, overview, plan_json),
    )
    return cur.lastrowid


def delete_week_plan(conn: sqlite3.Connection, week_start: str):
    conn.execute("DELETE FROM week_plans WHERE week_start = ?", (week_start,))


def fetch_tasks_scheduled_for_range(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    """All tasks scheduled within a date range, with project/goal context."""
    return conn.execute(
        """SELECT t.*, p.title AS project_title, g.title AS goal_title
           FROM tasks t
           JOIN projects p ON p.id = t.project_id
           JOIN goals g ON g.id = p.goal_id
           WHERE t.scheduled_date >= ? AND t.scheduled_date <= ?
           ORDER BY t.scheduled_date ASC, p.priority DESC""",
        (start, end),
    ).fetchall()


# ---------------------------------------------------------------------------
# Subtask helpers
# ---------------------------------------------------------------------------

def fetch_subtasks_for_task(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subtasks WHERE task_id = ? ORDER BY sort_order ASC, id ASC",
        (task_id,),
    ).fetchall()


def fetch_subtask(conn: sqlite3.Connection, subtask_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()


def insert_subtask(
    conn: sqlite3.Connection,
    task_id: int,
    title: str,
    description: str,
    estimated_minutes: int,
    sort_order: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO subtasks (task_id, title, description, estimated_minutes, sort_order)
           VALUES (?, ?, ?, ?, ?)""",
        (task_id, title, description, max(5, min(60, estimated_minutes)), sort_order),
    )
    return cur.lastrowid


def update_subtask(
    conn: sqlite3.Connection,
    subtask_id: int,
    title: str,
    description: str,
    estimated_minutes: int,
):
    conn.execute(
        "UPDATE subtasks SET title = ?, description = ?, estimated_minutes = ? WHERE id = ?",
        (title, description, max(5, min(60, estimated_minutes)), subtask_id),
    )


def update_subtask_status(conn: sqlite3.Connection, subtask_id: int, status: str):
    conn.execute("UPDATE subtasks SET status = ? WHERE id = ?", (status, subtask_id))


def update_subtask_notes(conn: sqlite3.Connection, subtask_id: int, notes: str):
    conn.execute("UPDATE subtasks SET notes = ? WHERE id = ?", (notes, subtask_id))


def delete_subtask(conn: sqlite3.Connection, subtask_id: int):
    conn.execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))


def reorder_subtasks(conn: sqlite3.Connection, task_id: int, ordered_ids: list[int]):
    for i, sid in enumerate(ordered_ids):
        conn.execute(
            "UPDATE subtasks SET sort_order = ? WHERE id = ? AND task_id = ?",
            (i, sid, task_id),
        )


def fetch_subtask_progress(conn: sqlite3.Connection, task_id: int) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(CASE WHEN status = 'done' THEN 1 END) AS done
           FROM subtasks WHERE task_id = ?""",
        (task_id,),
    ).fetchone()
    return {"total": row["total"], "done": row["done"]}


def fetch_subtask_progress_batch(conn: sqlite3.Connection, task_ids: list[int]) -> dict[int, dict]:
    """Fetch subtask progress for multiple tasks at once."""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"""SELECT task_id,
                   COUNT(*) AS total,
                   COUNT(CASE WHEN status = 'done' THEN 1 END) AS done
            FROM subtasks
            WHERE task_id IN ({placeholders})
            GROUP BY task_id""",
        task_ids,
    ).fetchall()
    return {r["task_id"]: {"total": r["total"], "done": r["done"]} for r in rows}


def next_sort_order(conn: sqlite3.Connection, task_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM subtasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return row["next_order"]


# ---------------------------------------------------------------------------
# Subtask template helpers
# ---------------------------------------------------------------------------

def fetch_all_templates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM subtask_templates ORDER BY name ASC").fetchall()


def fetch_template(conn: sqlite3.Connection, template_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM subtask_templates WHERE id = ?", (template_id,)).fetchone()


def _seed_default_templates(conn: sqlite3.Connection):
    import json as _json
    templates = [
        (
            "Build portfolio",
            "Steps to create a professional portfolio from scratch",
            [
                {"title": "Define target audience and goals", "description": "Who will see this? What impression should it give?", "estimated_minutes": 30},
                {"title": "Gather best work samples", "description": "Select 4-6 strongest pieces that demonstrate range", "estimated_minutes": 45},
                {"title": "Choose platform and hosting", "description": "Static site, Notion, Squarespace, GitHub Pages, etc.", "estimated_minutes": 30},
                {"title": "Design layout and information architecture", "description": "Sketch wireframes for homepage, project pages, about", "estimated_minutes": 45},
                {"title": "Write project descriptions and case studies", "description": "Context, process, outcomes for each piece", "estimated_minutes": 60},
                {"title": "Write bio and contact section", "description": "Professional summary, links, contact form", "estimated_minutes": 30},
                {"title": "Build and populate the site", "description": "Implement design, upload content, test navigation", "estimated_minutes": 60},
                {"title": "Get feedback and iterate", "description": "Share with 2-3 peers, collect feedback, refine", "estimated_minutes": 30},
            ],
        ),
        (
            "Write article",
            "Full writing workflow from research to publish",
            [
                {"title": "Research topic and collect sources", "description": "Find 3-5 credible sources, take notes on key points", "estimated_minutes": 45},
                {"title": "Create outline with key sections", "description": "Intro, main points, conclusion structure", "estimated_minutes": 20},
                {"title": "Write rough first draft", "description": "Get ideas down without editing, aim for completeness", "estimated_minutes": 60},
                {"title": "Revise for structure and flow", "description": "Reorder sections, strengthen transitions, cut fluff", "estimated_minutes": 30},
                {"title": "Edit for clarity and grammar", "description": "Tighten sentences, fix errors, improve word choice", "estimated_minutes": 25},
                {"title": "Add visuals or examples", "description": "Diagrams, screenshots, code samples as needed", "estimated_minutes": 30},
                {"title": "Final proofread and publish", "description": "One last pass, then format and publish", "estimated_minutes": 15},
            ],
        ),
        (
            "Learn a new skill",
            "Structured approach to learning something new",
            [
                {"title": "Define specific learning goal", "description": "What does 'knowing this' look like? Set a measurable target", "estimated_minutes": 15},
                {"title": "Find 2-3 quality learning resources", "description": "Course, book, tutorial — pick complementary formats", "estimated_minutes": 30},
                {"title": "Complete introductory material", "description": "Get through basics, build mental model of the domain", "estimated_minutes": 60},
                {"title": "Do first hands-on exercise", "description": "Apply what you learned in a small, guided exercise", "estimated_minutes": 45},
                {"title": "Build a small practice project", "description": "Something simple that uses the core concepts", "estimated_minutes": 60},
                {"title": "Review and fill knowledge gaps", "description": "What confused you? Go back and solidify weak areas", "estimated_minutes": 30},
                {"title": "Build a portfolio-worthy project", "description": "Larger project that demonstrates real competence", "estimated_minutes": 60},
            ],
        ),
        (
            "Plan an event",
            "Event planning from concept to follow-up",
            [
                {"title": "Define purpose and guest list", "description": "What's the occasion? Who needs to be there?", "estimated_minutes": 20},
                {"title": "Set date and book venue", "description": "Check availability, confirm space, send save-the-dates", "estimated_minutes": 30},
                {"title": "Create budget", "description": "List all costs: venue, food, supplies, decorations", "estimated_minutes": 20},
                {"title": "Plan agenda and activities", "description": "Timeline for the event, any speakers or activities", "estimated_minutes": 30},
                {"title": "Send invitations", "description": "Include date, time, location, RSVP details", "estimated_minutes": 20},
                {"title": "Arrange catering and supplies", "description": "Food, drinks, materials, equipment", "estimated_minutes": 30},
                {"title": "Confirm all details day before", "description": "Venue, vendors, attendees — final check", "estimated_minutes": 15},
                {"title": "Post-event follow-up", "description": "Thank attendees, collect feedback, settle payments", "estimated_minutes": 20},
            ],
        ),
        (
            "Job application",
            "Complete job application process for a single role",
            [
                {"title": "Research the company", "description": "Products, culture, recent news, team structure", "estimated_minutes": 30},
                {"title": "Tailor resume to posting", "description": "Match keywords, highlight relevant experience", "estimated_minutes": 40},
                {"title": "Write cover letter", "description": "Why this company, why this role, what you bring", "estimated_minutes": 35},
                {"title": "Prepare portfolio or work samples", "description": "Select and polish relevant examples", "estimated_minutes": 30},
                {"title": "Submit application", "description": "Double-check all materials, submit before deadline", "estimated_minutes": 10},
                {"title": "Prepare for interview", "description": "Practice answers, prepare questions to ask", "estimated_minutes": 45},
                {"title": "Send follow-up", "description": "Thank you email within 24 hours of interview", "estimated_minutes": 10},
            ],
        ),
        (
            "System design",
            "Architecture and design for a software system",
            [
                {"title": "Define requirements and constraints", "description": "Functional requirements, non-functional requirements, scale targets", "estimated_minutes": 30},
                {"title": "Identify core entities and data model", "description": "What data does the system manage? Relationships?", "estimated_minutes": 30},
                {"title": "Design high-level architecture", "description": "Components, services, data flow between them", "estimated_minutes": 45},
                {"title": "Draw system diagrams", "description": "Architecture diagram, sequence diagrams for key flows", "estimated_minutes": 40},
                {"title": "Design API contracts", "description": "Endpoints, request/response shapes, error handling", "estimated_minutes": 35},
                {"title": "Plan infrastructure and deployment", "description": "Hosting, CI/CD, monitoring, scaling strategy", "estimated_minutes": 30},
                {"title": "Identify risks and trade-offs", "description": "What could go wrong? What are you optimizing for vs. sacrificing?", "estimated_minutes": 20},
                {"title": "Write design document", "description": "Consolidate decisions into a shareable document", "estimated_minutes": 45},
            ],
        ),
    ]
    for name, desc, steps in templates:
        conn.execute(
            "INSERT OR IGNORE INTO subtask_templates (name, description, steps_json) VALUES (?, ?, ?)",
            (name, desc, _json.dumps(steps)),
        )
