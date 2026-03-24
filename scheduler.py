"""
scheduler.py — daily task selection logic.

get_daily_tasks(date) returns a dict:
  {
    "scheduled": [Task, ...],   # tasks for today (not done/skipped)
  }

Past unfinished tasks are silently rescheduled to today so they appear
without any negative "overdue" framing.

If fewer than 3 tasks are scheduled for today, high-priority unscheduled
todo tasks are pulled in and scheduled automatically.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import database as db
from models import Task


MIN_DAILY_TASKS = 3


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"],
        estimated_minutes=row["estimated_minutes"],
        status=row["status"],
        scheduled_date=row["scheduled_date"],
        created_at=row["created_at"],
        project_title=row["project_title"] if "project_title" in row.keys() else None,
        goal_title=row["goal_title"] if "goal_title" in row.keys() else None,
    )


def _enrich_task(conn: sqlite3.Connection, task: Task) -> Task:
    """Attach project/goal title and active session info."""
    row = conn.execute(
        """SELECT p.title AS project_title, g.title AS goal_title
           FROM projects p JOIN goals g ON g.id = p.goal_id
           WHERE p.id = ?""",
        (task.project_id,),
    ).fetchone()
    if row:
        task.project_title = row["project_title"]
        task.goal_title = row["goal_title"]

    session = db.fetch_active_session_for_task(conn, task.id)
    if session:
        task.active_session_id = session["id"]
        task.active_session_started_at = session["started_at"]

    return task


def get_daily_tasks(target_date: date | None = None) -> dict:
    if target_date is None:
        target_date = date.today()
    date_str = target_date.isoformat()

    conn = db.get_db()
    try:
        # Silently roll past unfinished scheduled tasks forward to today
        conn.execute(
            """UPDATE tasks SET scheduled_date = ?
               WHERE scheduled_date < ?
                 AND status NOT IN ('done', 'skipped')""",
            (date_str, date_str),
        )
        conn.commit()

        # Tasks scheduled for today (not done/skipped)
        scheduled_rows = conn.execute(
            """SELECT t.*, p.title AS project_title, g.title AS goal_title
               FROM tasks t
               JOIN projects p ON p.id = t.project_id
               JOIN goals    g ON g.id = p.goal_id
               WHERE t.scheduled_date = ?
                 AND t.status NOT IN ('done', 'skipped')
               ORDER BY p.priority DESC, t.created_at ASC""",
            (date_str,),
        ).fetchall()

        scheduled: list[Task] = []
        for r in scheduled_rows:
            t = _row_to_task(r)
            scheduled.append(_enrich_task(conn, t))

        # Auto-fill up to MIN_DAILY_TASKS from unscheduled backlog
        if len(scheduled) < MIN_DAILY_TASKS:
            needed = MIN_DAILY_TASKS - len(scheduled)
            existing_ids = {t.id for t in scheduled}

            candidates = conn.execute(
                """SELECT t.*, p.title AS project_title, g.title AS goal_title
                   FROM tasks t
                   JOIN projects p ON p.id = t.project_id
                   JOIN goals    g ON g.id = p.goal_id
                   WHERE t.status = 'todo'
                     AND (t.scheduled_date IS NULL OR t.scheduled_date = '')
                     AND p.active = 1
                     AND g.active = 1
                   ORDER BY p.priority DESC, t.created_at ASC
                   LIMIT ?""",
                (needed * 3,),
            ).fetchall()

            added = 0
            for r in candidates:
                if added >= needed:
                    break
                if r["id"] in existing_ids:
                    continue
                conn.execute(
                    "UPDATE tasks SET scheduled_date = ? WHERE id = ?",
                    (date_str, r["id"]),
                )
                t = _row_to_task(r)
                t.scheduled_date = date_str
                scheduled.append(_enrich_task(conn, t))
                existing_ids.add(t.id)
                added += 1

            conn.commit()

        return {"scheduled": scheduled}
    finally:
        conn.close()
