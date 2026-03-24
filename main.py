from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import ai
import database as db
import scheduler as sched
from database import db_context

app = FastAPI(title="Life Manager")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup():
    db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday_of(d: date) -> date:
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _parse_briefing_data(briefing_row) -> tuple[str, dict, str | None, str | None]:
    """Parse a briefing row into (greeting, task_reasons, coaching_note, motivation).

    Handles both old format (list of {id, reason}) and new format (structured dict).
    """
    greeting = briefing_row["message"]
    raw = json.loads(briefing_row["tasks_json"])

    # New structured format
    if isinstance(raw, dict) and "schedule" in raw:
        task_reasons = {}
        task_time_blocks = {}
        for item in raw["schedule"]:
            tid = item.get("task_id")
            if tid is not None:
                task_reasons[tid] = item.get("reason", "")
                task_time_blocks[tid] = item.get("time_block", "")
        return (
            greeting,
            task_reasons,
            task_time_blocks,
            raw.get("coaching_note"),
            raw.get("motivation"),
        )

    # Old flat format: [{"id": ..., "reason": ...}]
    if isinstance(raw, list):
        task_reasons = {t["id"]: t.get("reason", "") for t in raw}
        return greeting, task_reasons, {}, None, None

    return greeting, {}, {}, None, None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, completed: int = 0, nofocus: int = 0):
    # Check for active focus session
    with db_context() as conn:
        active = db.fetch_active_session_any(conn)

    if active and not nofocus:
        return templates.TemplateResponse(
            request,
            "focus.html",
            {
                "task_title": active["task_title"],
                "task_description": active["task_description"],
                "goal_title": active["goal_title"],
                "project_title": active["project_title"],
                "session_id": active["id"],
                "task_id": active["task_id"],
                "started_at": active["started_at"],
                "estimated_minutes": active["estimated_minutes"],
            },
        )

    # Normal dashboard
    today = date.today()
    date_str = today.isoformat()

    greeting = None
    task_reasons = {}
    task_time_blocks = {}
    coaching_note = None
    motivation = None

    with db_context() as conn:
        performance = db.fetch_performance_stats(conn)
        briefing = db.fetch_briefing_for_date(conn, date_str)
        if briefing:
            greeting, task_reasons, task_time_blocks, coaching_note, motivation = (
                _parse_briefing_data(briefing)
            )

    daily = sched.get_daily_tasks(today)

    with db_context() as conn:
        week_sessions = db.count_sessions_this_week(conn)
        week_done = db.count_tasks_done_this_week(conn)
        active_session = db.fetch_active_session_any(conn)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "today": today.strftime("%A, %B %-d"),
            "scheduled": daily["scheduled"],
            "week_sessions": week_sessions,
            "week_done": week_done,
            "greeting": greeting,
            "coaching_note": coaching_note,
            "motivation": motivation,
            "task_reasons": task_reasons,
            "task_time_blocks": task_time_blocks,
            "performance": performance,
            "ai_enabled": ai.is_enabled(),
            "completed": completed,
            "active_session": active_session,
        },
    )


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Work Journal
# ---------------------------------------------------------------------------

@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request, days: int = 14):
    with db_context() as conn:
        sessions = conn.execute(
            """SELECT s.started_at, s.ended_at, s.notes,
                      CAST((julianday(COALESCE(s.ended_at, datetime('now'))) - julianday(s.started_at)) * 24 * 60 AS INTEGER) AS minutes,
                      t.id AS task_id, t.title AS task_title,
                      p.title AS project_title, g.title AS goal_title
               FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               JOIN projects p ON p.id = t.project_id
               JOIN goals g ON g.id = p.goal_id
               WHERE s.ended_at IS NOT NULL
                 AND date(s.started_at) >= date('now', ?)
               ORDER BY s.started_at DESC""",
            (f"-{days} days",),
        ).fetchall()

        completions = conn.execute(
            """SELECT t.title AS task_title, p.title AS project_title,
                      g.title AS goal_title, t.status,
                      COALESCE(t.scheduled_date, date(t.created_at)) AS done_date
               FROM tasks t
               JOIN projects p ON p.id = t.project_id
               JOIN goals g ON g.id = p.goal_id
               WHERE t.status = 'done'
                 AND COALESCE(t.scheduled_date, date(t.created_at)) >= date('now', ?)
               ORDER BY done_date DESC""",
            (f"-{days} days",),
        ).fetchall()

    # Group sessions by day
    from collections import OrderedDict
    days_map = OrderedDict()
    for s in sessions:
        day = s["started_at"][:10]
        days_map.setdefault(day, {"sessions": [], "total_minutes": 0})
        days_map[day]["sessions"].append(dict(s))
        days_map[day]["total_minutes"] += s["minutes"] or 0

    # Group completions by day
    comp_map = {}
    for c in completions:
        day = c["done_date"]
        comp_map.setdefault(day, []).append(dict(c))

    # Merge all days
    all_days = sorted(set(list(days_map.keys()) + list(comp_map.keys())), reverse=True)
    journal_days = []
    for day in all_days:
        journal_days.append({
            "date": day,
            "sessions": days_map.get(day, {}).get("sessions", []),
            "total_minutes": days_map.get(day, {}).get("total_minutes", 0),
            "completions": comp_map.get(day, []),
        })

    return templates.TemplateResponse(
        request,
        "journal.html",
        {"journal_days": journal_days, "days_range": days},
    )


@app.get("/journal/export.md")
async def journal_export(days: int = 14):
    with db_context() as conn:
        sessions = conn.execute(
            """SELECT s.started_at, s.ended_at, s.notes,
                      CAST((julianday(COALESCE(s.ended_at, datetime('now'))) - julianday(s.started_at)) * 24 * 60 AS INTEGER) AS minutes,
                      t.title AS task_title, p.title AS project_title, g.title AS goal_title
               FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               JOIN projects p ON p.id = t.project_id
               JOIN goals g ON g.id = p.goal_id
               WHERE s.ended_at IS NOT NULL
                 AND date(s.started_at) >= date('now', ?)
               ORDER BY s.started_at DESC""",
            (f"-{days} days",),
        ).fetchall()

    from collections import OrderedDict
    days_map = OrderedDict()
    for s in sessions:
        day = s["started_at"][:10]
        days_map.setdefault(day, []).append(s)

    lines = [f"# Work Journal", f"", f"*Last {days} days — exported {date.today().isoformat()}*", ""]
    grand_total = 0
    for day, sess_list in days_map.items():
        day_total = sum(s["minutes"] or 0 for s in sess_list)
        grand_total += day_total
        lines.append(f"## {day} ({day_total} min)")
        lines.append("")
        for s in sess_list:
            started = s["started_at"][11:16]
            ended = s["ended_at"][11:16] if s["ended_at"] else "?"
            lines.append(f"- **{started}–{ended}** ({s['minutes']}m) — {s['task_title']} *({s['project_title']} → {s['goal_title']})*")
            if s["notes"]:
                lines.append(f"  > {s['notes']}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"**Total:** {grand_total} min across {len(days_map)} days")

    filename = f"{date.today().isoformat()}-journal.md"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/briefing/generate")
async def generate_briefing():
    today = date.today()
    date_str = today.isoformat()
    with db_context() as conn:
        db.delete_briefing_for_date(conn, date_str)
        hierarchy = db.fetch_full_hierarchy(conn)
        completions = [dict(r) for r in db.fetch_recent_completions(conn)]
        performance = db.fetch_performance_stats(conn)

    result = ai.generate_daily_briefing(hierarchy, completions, performance, today)

    if result:
        with db_context() as conn:
            for item in result.get("schedule", []):
                tid = item.get("task_id")
                if tid:
                    task_row = db.fetch_task(conn, tid)
                    if task_row and task_row["status"] == "todo":
                        db.schedule_task(conn, tid, date_str)
            briefing_data = {
                "schedule": result.get("schedule", []),
                "coaching_note": result.get("coaching_note"),
                "motivation": result.get("motivation"),
            }
            db.insert_briefing(conn, date_str, result.get("greeting", ""), json.dumps(briefing_data))

    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Week View
# ---------------------------------------------------------------------------

@app.get("/week", response_class=HTMLResponse)
async def week_page(request: Request):
    today = date.today()
    monday = _monday_of(today)
    sunday = monday + timedelta(days=6)
    week_start_str = monday.isoformat()

    with db_context() as conn:
        plan_row = db.fetch_week_plan(conn, week_start_str)
        scheduled = db.fetch_tasks_scheduled_for_range(
            conn, monday.isoformat(), sunday.isoformat()
        )
        performance = db.fetch_performance_stats(conn)
        unscheduled = conn.execute(
            """SELECT t.id, t.title, p.title AS project_title
               FROM tasks t JOIN projects p ON p.id = t.project_id
               WHERE (t.scheduled_date IS NULL OR t.scheduled_date = '')
                 AND t.status = 'todo'
               ORDER BY p.priority DESC, t.created_at ASC"""
        ).fetchall()

    # Build days structure
    days = []
    scheduled_by_date = {}
    for t in scheduled:
        d = t["scheduled_date"]
        scheduled_by_date.setdefault(d, []).append(dict(t))

    plan_data = None
    overview = None
    if plan_row:
        overview = plan_row["overview"]
        plan_data = json.loads(plan_row["plan_json"])

    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = d.isoformat()
        day_plan = None
        if plan_data:
            for pd in plan_data.get("days", []):
                if pd.get("date") == d_str:
                    day_plan = pd
                    break
        days.append({
            "date": d,
            "date_str": d_str,
            "name": d.strftime("%a"),
            "day_num": d.day,
            "is_today": d == today,
            "tasks": scheduled_by_date.get(d_str, []),
            "theme": day_plan.get("theme") if day_plan else None,
        })

    return templates.TemplateResponse(
        request,
        "week.html",
        {
            "days": days,
            "overview": overview,
            "week_label": f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d')}",
            "week_start": week_start_str,
            "has_plan": plan_row is not None,
            "ai_enabled": ai.is_enabled(),
            "performance": performance,
            "unscheduled": [dict(r) for r in unscheduled],
        },
    )


@app.post("/week/schedule", response_class=HTMLResponse)
async def week_schedule_task(task_id: int = Form(...), scheduled_date: str = Form(...)):
    with db_context() as conn:
        db.schedule_task(conn, task_id, scheduled_date)
    return RedirectResponse("/week", status_code=303)


@app.post("/week/plan")
async def generate_week_plan_endpoint():
    today = date.today()
    monday = _monday_of(today)
    week_start_str = monday.isoformat()

    with db_context() as conn:
        # Clear old plan
        db.delete_week_plan(conn, week_start_str)
        hierarchy = db.fetch_full_hierarchy(conn)
        performance = db.fetch_performance_stats(conn)

    plan = ai.generate_week_plan(hierarchy, performance, monday, days=7)

    if plan:
        with db_context() as conn:
            # Schedule the tasks for their assigned days
            for day_data in plan.get("days", []):
                day_date = day_data.get("date")
                if not day_date:
                    continue
                for item in day_data.get("tasks", []):
                    tid = item.get("task_id")
                    if tid:
                        task_row = db.fetch_task(conn, tid)
                        if task_row and task_row["status"] == "todo":
                            db.schedule_task(conn, tid, day_date)
            db.insert_week_plan(
                conn, week_start_str,
                plan.get("overview", ""),
                json.dumps(plan),
            )
        # Also regenerate today's briefing to match the week plan
        with db_context() as conn:
            db.delete_briefing_for_date(conn, today.isoformat())

    return RedirectResponse("/week", status_code=303)


@app.post("/week/regenerate")
async def regenerate_week_plan():
    monday = _monday_of(date.today())
    with db_context() as conn:
        db.delete_week_plan(conn, monday.isoformat())
    return RedirectResponse("/week", status_code=303)


# ---------------------------------------------------------------------------
# Weekly Review
# ---------------------------------------------------------------------------

@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    with db_context() as conn:
        completions = [dict(r) for r in db.fetch_recent_completions(conn)]
        sessions = db.fetch_sessions_summary_this_week(conn)
        week_done = db.count_tasks_done_this_week(conn)
        week_sessions = db.count_sessions_this_week(conn)

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "completions": completions,
            "week_done": week_done,
            "week_sessions": week_sessions,
            "total_minutes": sessions["total_minutes"],
            "ai_enabled": ai.is_enabled(),
        },
    )


@app.post("/review/generate", response_class=HTMLResponse)
async def review_generate(request: Request):
    today = date.today()
    with db_context() as conn:
        hierarchy = db.fetch_full_hierarchy(conn)
        completions = [dict(r) for r in db.fetch_recent_completions(conn)]
        sessions = db.fetch_sessions_summary_this_week(conn)

    review_text = ai.generate_weekly_review(hierarchy, completions, sessions, today)

    if not review_text:
        return HTMLResponse(
            '<p class="text-muted text-sm">Failed to generate review. Try again.</p>'
        )
    return HTMLResponse(
        f'<div style="font-size:0.92rem; line-height:1.7; white-space:pre-line;">{review_text}</div>'
    )


# ---------------------------------------------------------------------------
# Goal planning
# ---------------------------------------------------------------------------

@app.get("/goals/{goal_id}/export.md")
async def goal_export_md(goal_id: int):
    with db_context() as conn:
        goal = db.fetch_goal(conn, goal_id)
        if not goal:
            return PlainTextResponse("Goal not found", status_code=404)
        projects = db.fetch_projects_for_goal(conn, goal_id)

    lines = [f"# Goal: {goal['title']}", ""]
    if goal["description"]:
        lines.append(goal["description"])
        lines.append("")
    lines.append(f"**Priority:** {goal['priority']}/5  ")
    lines.append(f"**Exported:** {date.today().isoformat()}  ")
    lines.append("")

    for project in projects:
        with db_context() as conn:
            tasks = db.fetch_tasks_for_project(conn, project["id"])

        done = sum(1 for t in tasks if t["status"] == "done")
        total = len(tasks)
        pct = round(done / total * 100) if total > 0 else 0

        lines.append(f"## {project['title']} ({done}/{total} — {pct}%)")
        lines.append("")
        if project["description"]:
            lines.append(project["description"])
            lines.append("")
        if project["deadline"]:
            lines.append(f"*Deadline: {project['deadline']}*")
            lines.append("")

        for task in tasks:
            icon = {"done": "✅", "skipped": "⏭️", "in_progress": "🔄"}.get(task["status"], "⬜")
            lines.append(f"- {icon} **{task['title']}** ({task['estimated_minutes']}m)")
            if task["description"]:
                lines.append(f"  {task['description']}")

        lines.append("")

    # Summary stats
    with db_context() as conn:
        total_sessions = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(CAST((julianday(ended_at) - julianday(started_at)) * 24 * 60 AS INTEGER)), 0) AS mins
               FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               JOIN projects p ON p.id = t.project_id
               WHERE p.goal_id = ? AND s.ended_at IS NOT NULL""",
            (goal_id,),
        ).fetchone()

    lines.append("---")
    lines.append(f"**Total sessions:** {total_sessions['cnt']}  ")
    lines.append(f"**Total focused time:** {total_sessions['mins']} min")

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in goal["title"])
    filename = f"{date.today().isoformat()}-goal-{safe_title[:50]}.md"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/goals/{goal_id}/plan", response_class=HTMLResponse)
async def goal_plan_page(goal_id: int, request: Request):
    with db_context() as conn:
        goal = db.fetch_goal(conn, goal_id)
        if not goal:
            return RedirectResponse("/goals", status_code=303)

    return templates.TemplateResponse(
        request,
        "goal_plan.html",
        {"goal": goal, "plan": None, "error": None, "ai_enabled": ai.is_enabled()},
    )


@app.post("/goals/{goal_id}/plan", response_class=HTMLResponse)
async def goal_plan_generate(goal_id: int, request: Request):
    with db_context() as conn:
        goal = db.fetch_goal(conn, goal_id)
        if not goal:
            return RedirectResponse("/goals", status_code=303)
        existing_projects = db.fetch_projects_for_goal(conn, goal_id)

    plan = None
    error = None
    if ai.is_enabled():
        plan = ai.generate_goal_roadmap(dict(goal), [dict(p) for p in existing_projects])
        if not plan:
            error = "AI failed to generate a plan. Try again."
    else:
        error = "Set an AI provider key to enable planning."

    return templates.TemplateResponse(
        request,
        "goal_plan.html",
        {"goal": goal, "plan": plan, "error": error, "ai_enabled": ai.is_enabled()},
    )


@app.post("/goals/{goal_id}/apply-plan", response_class=HTMLResponse)
async def apply_goal_plan(
    goal_id: int,
    selected_json: str = Form(...),
):
    data = json.loads(selected_json)

    with db_context() as conn:
        for project_data in data.get("projects", []):
            proj_id = db.insert_project(
                conn,
                goal_id,
                project_data["title"],
                project_data.get("description", ""),
                None,
                project_data.get("priority", 3),
            )
            for task_data in project_data.get("tasks", []):
                db.insert_task(
                    conn,
                    proj_id,
                    task_data["title"],
                    task_data.get("description", ""),
                    task_data.get("estimated_minutes", 45),
                )

    return RedirectResponse("/goals", status_code=303)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    with db_context() as conn:
        goals = db.fetch_all_goals(conn)
        goals_with_projects = []
        for g in goals:
            projects = db.fetch_projects_for_goal(conn, g["id"])
            projects_with_tasks = []
            for p in projects:
                tasks = db.fetch_tasks_for_project(conn, p["id"])
                projects_with_tasks.append({"project": p, "tasks": tasks})
            goals_with_projects.append({"goal": g, "projects": projects_with_tasks})

    return templates.TemplateResponse(
        request,
        "goals.html",
        {"goals_with_projects": goals_with_projects},
    )


@app.post("/goals", response_class=HTMLResponse)
async def create_goal(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    priority: int = Form(3),
):
    with db_context() as conn:
        goal_id = db.insert_goal(conn, title, description, priority)
        goal = db.fetch_goal(conn, goal_id)
        projects_with_tasks = []

    return templates.TemplateResponse(
        request,
        "partials/goal_item.html",
        {"goal": goal, "projects": projects_with_tasks},
    )


@app.post("/goals/{goal_id}/delete", response_class=HTMLResponse)
async def delete_goal(goal_id: int):
    with db_context() as conn:
        db.delete_goal(conn, goal_id)
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    with db_context() as conn:
        goals = db.fetch_all_goals(conn)
        groups = []
        for g in goals:
            projects = db.fetch_projects_for_goal(conn, g["id"])
            if projects:
                projects_with_tasks = []
                for p in projects:
                    tasks = db.fetch_tasks_for_project(conn, p["id"])
                    projects_with_tasks.append({"project": p, "tasks": tasks})
                groups.append({"goal": g, "projects": projects_with_tasks})
    return templates.TemplateResponse(request, "projects.html", {"groups": groups})


@app.get("/projects/{project_id}/export.md")
async def project_export_md(project_id: int):
    with db_context() as conn:
        project = db.fetch_project(conn, project_id)
        if not project:
            return PlainTextResponse("Project not found", status_code=404)
        goal = db.fetch_goal(conn, project["goal_id"])
        tasks = db.fetch_tasks_for_project(conn, project_id)

    lines = [f"# {project['title']}", ""]
    lines.append(f"**Goal:** {goal['title']}  ")
    if project["description"]:
        lines.append(f"**Description:** {project['description']}  ")
    lines.append(f"**Priority:** {project['priority']}/5  ")
    if project["deadline"]:
        lines.append(f"**Deadline:** {project['deadline']}  ")
    lines.append(f"**Exported:** {date.today().isoformat()}  ")
    lines.append("")

    done_count = sum(1 for t in tasks if t["status"] == "done")
    lines.append(f"**Progress:** {done_count}/{len(tasks)} tasks completed")
    lines.append("")

    for task in tasks:
        status_icon = {"done": "✅", "skipped": "⏭️", "in_progress": "🔄"}.get(task["status"], "⬜")
        lines.append(f"## {status_icon} {task['title']}")
        lines.append("")
        if task["description"]:
            lines.append(task["description"])
            lines.append("")
        lines.append(f"*Status: {task['status']} · Estimated: {task['estimated_minutes']}m*")
        lines.append("")

        with db_context() as conn:
            subtasks = db.fetch_subtasks_for_task(conn, task["id"])
            sessions = conn.execute(
                """SELECT started_at, ended_at, notes,
                          CAST((julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 24 * 60 AS INTEGER) AS minutes
                   FROM sessions WHERE task_id = ? ORDER BY started_at ASC""",
                (task["id"],),
            ).fetchall()

        if subtasks:
            lines.append("### Steps")
            lines.append("")
            for i, s in enumerate(subtasks, 1):
                si = "✅" if s["status"] == "done" else ("⏭️" if s["status"] == "skipped" else "⬜")
                lines.append(f"**{si} Step {i}: {s['title']}**")
                if s["description"]:
                    lines.append(f"*{s['description']}*")
                if s["notes"] and s["notes"].strip():
                    lines.append("")
                    lines.append(s["notes"].strip())
                lines.append(f"*{s['estimated_minutes']}m*")
                lines.append("")

        if sessions:
            lines.append("### Sessions")
            lines.append("")
            for s in sessions:
                started = s["started_at"][:16].replace("T", " ")
                ended = "ongoing" if not s["ended_at"] else s["ended_at"][:16].replace("T", " ")
                lines.append(f"- {started} → {ended} ({s['minutes']}m)")
                if s["notes"]:
                    lines.append(f"  > {s['notes']}")
            lines.append("")

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in project["title"])
    filename = f"{date.today().isoformat()}-{safe_title[:50]}.md"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_page(project_id: int, request: Request):
    with db_context() as conn:
        project = db.fetch_project(conn, project_id)
        if not project:
            return RedirectResponse("/goals", status_code=303)
        goal = db.fetch_goal(conn, project["goal_id"])
        tasks = db.fetch_tasks_for_project(conn, project_id)
        task_ids = [t["id"] for t in tasks]
        progress_map = db.fetch_subtask_progress_batch(conn, task_ids)
        enriched = []
        for t in tasks:
            td = dict(t)
            prog = progress_map.get(t["id"], {"total": 0, "done": 0})
            td["subtask_total"] = prog["total"]
            td["subtask_done"] = prog["done"]
            enriched.append(td)
    return templates.TemplateResponse(
        request,
        "project.html",
        {"project": project, "goal": goal, "tasks": enriched},
    )


@app.post("/projects", response_class=HTMLResponse)
async def create_project(
    request: Request,
    goal_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    deadline: str = Form(""),
    priority: int = Form(3),
):
    with db_context() as conn:
        proj_id = db.insert_project(
            conn, goal_id, title, description, deadline or None, priority
        )
        project = db.fetch_project(conn, proj_id)

    return templates.TemplateResponse(
        request,
        "partials/project_item.html",
        {"project": project},
    )


@app.post("/projects/{project_id}/delete", response_class=HTMLResponse)
async def delete_project(project_id: int):
    with db_context() as conn:
        db.delete_project(conn, project_id)
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, status: Optional[str] = None):
    with db_context() as conn:
        all_projects = db.fetch_all_projects(conn)
        projects_with_tasks = []
        for p in all_projects:
            tasks = db.fetch_tasks_for_project(conn, p["id"])
            if status:
                tasks = [t for t in tasks if t["status"] == status]
            # Attach subtask progress
            task_ids = [t["id"] for t in tasks]
            progress_map = db.fetch_subtask_progress_batch(conn, task_ids)
            enriched = []
            for t in tasks:
                td = dict(t)
                prog = progress_map.get(t["id"], {"total": 0, "done": 0})
                td["subtask_total"] = prog["total"]
                td["subtask_done"] = prog["done"]
                enriched.append(td)
            projects_with_tasks.append({"project": p, "tasks": enriched})

    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "projects_with_tasks": projects_with_tasks,
            "all_projects": all_projects,
            "current_status": status,
        },
    )


@app.post("/tasks", response_class=HTMLResponse)
async def create_task(
    request: Request,
    project_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    estimated_minutes: int = Form(45),
):
    with db_context() as conn:
        task_id = db.insert_task(conn, project_id, title, description, estimated_minutes)
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, project_id)

    return templates.TemplateResponse(
        request,
        "partials/task_row.html",
        {"task": task, "project": project},
    )


@app.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
async def task_edit(
    task_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    estimated_minutes: int = Form(45),
):
    with db_context() as conn:
        db.update_task(conn, task_id, title, description, estimated_minutes)
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])
        prog = db.fetch_subtask_progress(conn, task_id)
        td = dict(task)
        td["subtask_total"] = prog["total"]
        td["subtask_done"] = prog["done"]
    return templates.TemplateResponse(
        request, "partials/task_row.html", {"task": td, "project": project}
    )


@app.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
async def task_delete(task_id: int):
    with db_context() as conn:
        db.delete_task(conn, task_id)
    return HTMLResponse("")


@app.post("/tasks/{task_id}/pause", response_class=HTMLResponse)
async def task_pause(task_id: int):
    """End any active session and return the task to the backlog (todo, no date)."""
    with db_context() as conn:
        session = db.fetch_active_session_for_task(conn, task_id)
        if session:
            db.end_session(conn, session["id"], None)
        db.update_task_status(conn, task_id, "todo")
        conn.execute("UPDATE tasks SET scheduled_date = NULL WHERE id = ?", (task_id,))
    return HTMLResponse("", headers={"HX-Redirect": "/"})


@app.post("/tasks/{task_id}/schedule", response_class=HTMLResponse)
async def task_schedule(task_id: int, request: Request, scheduled_date: str = Form(...)):
    with db_context() as conn:
        db.schedule_task(conn, task_id, scheduled_date)
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])
        prog = db.fetch_subtask_progress(conn, task_id)
        td = dict(task)
        td["subtask_total"] = prog["total"]
        td["subtask_done"] = prog["done"]
    return templates.TemplateResponse(
        request, "partials/task_row.html", {"task": td, "project": project}
    )


@app.post("/tasks/{task_id}/unschedule", response_class=HTMLResponse)
async def task_unschedule(task_id: int, request: Request):
    with db_context() as conn:
        conn.execute("UPDATE tasks SET scheduled_date = NULL WHERE id = ?", (task_id,))
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])
        prog = db.fetch_subtask_progress(conn, task_id)
        td = dict(task)
        td["subtask_total"] = prog["total"]
        td["subtask_done"] = prog["done"]
    return templates.TemplateResponse(
        request, "partials/task_row.html", {"task": td, "project": project}
    )


@app.post("/tasks/{task_id}/todo", response_class=HTMLResponse)
async def task_todo(task_id: int, request: Request):
    with db_context() as conn:
        db.update_task_status(conn, task_id, "todo")
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])
        prog = db.fetch_subtask_progress(conn, task_id)
        td = dict(task)
        td["subtask_total"] = prog["total"]
        td["subtask_done"] = prog["done"]
    return templates.TemplateResponse(
        request, "partials/task_row.html", {"task": td, "project": project}
    )


@app.post("/tasks/{task_id}/done", response_class=HTMLResponse)
async def task_done(task_id: int, request: Request):
    with db_context() as conn:
        db.update_task_status(conn, task_id, "done")
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])

    return templates.TemplateResponse(
        request,
        "partials/task_card.html",
        {"task": task, "project": project, "session": None},
    )


@app.post("/tasks/{task_id}/skip", response_class=HTMLResponse)
async def task_skip(task_id: int, request: Request):
    with db_context() as conn:
        db.update_task_status(conn, task_id, "skipped")
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])

    return templates.TemplateResponse(
        request,
        "partials/task_card.html",
        {"task": task, "project": project, "session": None},
    )


@app.post("/tasks/{task_id}/start", response_class=HTMLResponse)
async def task_start(task_id: int, request: Request):
    with db_context() as conn:
        any_active = db.fetch_active_session_any(conn)
        if any_active:
            db.end_session(conn, any_active["id"], None)
        db.update_task_status(conn, task_id, "in_progress")
        db.insert_session(conn, task_id)

    return HTMLResponse("", headers={"HX-Redirect": "/"})


# ---------------------------------------------------------------------------
# Task planning (subtasks)
# ---------------------------------------------------------------------------

@app.get("/tasks/{task_id}/plan", response_class=HTMLResponse)
async def task_plan_page(task_id: int, request: Request):
    with db_context() as conn:
        task = db.fetch_task(conn, task_id)
        if not task:
            return RedirectResponse("/tasks", status_code=303)
        project = db.fetch_project(conn, task["project_id"])
        goal = conn.execute(
            "SELECT g.* FROM goals g JOIN projects p ON p.goal_id = g.id WHERE p.id = ?",
            (project["id"],),
        ).fetchone()
        subtasks = db.fetch_subtasks_for_task(conn, task_id)
        progress = db.fetch_subtask_progress(conn, task_id)
        all_templates = db.fetch_all_templates(conn)

    return templates.TemplateResponse(
        request,
        "task_plan.html",
        {
            "task": task,
            "project": project,
            "goal": goal,
            "subtasks": subtasks,
            "progress": progress,
            "templates": all_templates,
            "ai_enabled": ai.is_enabled(),
        },
    )


@app.get("/tasks/{task_id}/export.md")
async def task_export_md(task_id: int):
    with db_context() as conn:
        task = db.fetch_task(conn, task_id)
        if not task:
            return PlainTextResponse("Task not found", status_code=404)
        project = db.fetch_project(conn, task["project_id"])
        goal = conn.execute(
            "SELECT g.* FROM goals g JOIN projects p ON p.goal_id = g.id WHERE p.id = ?",
            (project["id"],),
        ).fetchone()
        subtasks = db.fetch_subtasks_for_task(conn, task_id)
        sessions = conn.execute(
            """SELECT started_at, ended_at, notes,
                      CAST((julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 24 * 60 AS INTEGER) AS minutes
               FROM sessions WHERE task_id = ? ORDER BY started_at ASC""",
            (task_id,),
        ).fetchall()

    lines = []

    # Header
    lines.append(f"# {task['title']}")
    lines.append("")
    lines.append(f"**Goal:** {goal['title']}  ")
    lines.append(f"**Project:** {project['title']}  ")
    lines.append(f"**Status:** {task['status'].replace('_', ' ')}  ")
    lines.append(f"**Estimated:** {task['estimated_minutes']} min  ")
    lines.append(f"**Exported:** {date.today().isoformat()}  ")
    lines.append("")

    if task["description"]:
        lines.append("## Overview")
        lines.append("")
        lines.append(task["description"])
        lines.append("")

    # Steps
    if subtasks:
        lines.append("## Steps")
        lines.append("")
        for i, s in enumerate(subtasks, 1):
            status_icon = "✅" if s["status"] == "done" else ("⏭️" if s["status"] == "skipped" else "⬜")
            lines.append(f"### {status_icon} Step {i}: {s['title']}")
            lines.append("")
            if s["description"]:
                lines.append(f"*{s['description']}*")
                lines.append("")
            if s["notes"] and s["notes"].strip():
                lines.append(s["notes"].strip())
                lines.append("")
            lines.append(f"*Estimated: {s['estimated_minutes']} min*")
            lines.append("")

    # Sessions
    if sessions:
        lines.append("## Focus Sessions")
        lines.append("")
        total_min = 0
        for s in sessions:
            started = s["started_at"][:16].replace("T", " ")
            mins = s["minutes"] or 0
            total_min += mins
            ended = "ongoing" if not s["ended_at"] else s["ended_at"][:16].replace("T", " ")
            lines.append(f"- **{started}** → {ended} ({mins} min)")
            if s["notes"]:
                lines.append(f"  > {s['notes']}")
        lines.append("")
        lines.append(f"**Total focused time:** {total_min} min")
        lines.append("")

    content = "\n".join(lines)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in task["title"])
    filename = f"{date.today().isoformat()}-{safe_title[:50]}.md"

    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/tasks/{task_id}/subtasks", response_class=HTMLResponse)
async def create_subtask(
    task_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    estimated_minutes: int = Form(15),
):
    with db_context() as conn:
        sort_order = db.next_sort_order(conn, task_id)
        sub_id = db.insert_subtask(conn, task_id, title, description, estimated_minutes, sort_order)
        subtask = db.fetch_subtask(conn, sub_id)

    return templates.TemplateResponse(
        request,
        "partials/subtask_item.html",
        {"subtask": subtask},
    )


@app.post("/subtasks/{subtask_id}/done", response_class=HTMLResponse)
async def subtask_done(subtask_id: int, request: Request):
    with db_context() as conn:
        db.update_subtask_status(conn, subtask_id, "done")
        subtask = db.fetch_subtask(conn, subtask_id)
    return templates.TemplateResponse(request, "partials/subtask_item.html", {"subtask": subtask})


@app.post("/subtasks/{subtask_id}/undo", response_class=HTMLResponse)
async def subtask_undo(subtask_id: int, request: Request):
    with db_context() as conn:
        db.update_subtask_status(conn, subtask_id, "todo")
        subtask = db.fetch_subtask(conn, subtask_id)
    return templates.TemplateResponse(request, "partials/subtask_item.html", {"subtask": subtask})


@app.post("/subtasks/{subtask_id}/skip", response_class=HTMLResponse)
async def subtask_skip(subtask_id: int, request: Request):
    with db_context() as conn:
        db.update_subtask_status(conn, subtask_id, "skipped")
        subtask = db.fetch_subtask(conn, subtask_id)
    return templates.TemplateResponse(request, "partials/subtask_item.html", {"subtask": subtask})


@app.post("/subtasks/{subtask_id}/notes", response_class=HTMLResponse)
async def subtask_save_notes(subtask_id: int, notes: str = Form("")):
    with db_context() as conn:
        db.update_subtask_notes(conn, subtask_id, notes)
    return HTMLResponse("")


@app.post("/subtasks/{subtask_id}/edit", response_class=HTMLResponse)
async def subtask_edit(
    subtask_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    estimated_minutes: int = Form(15),
):
    with db_context() as conn:
        db.update_subtask(conn, subtask_id, title, description, estimated_minutes)
        subtask = db.fetch_subtask(conn, subtask_id)
    return templates.TemplateResponse(request, "partials/subtask_item.html", {"subtask": subtask})


@app.post("/subtasks/{subtask_id}/delete", response_class=HTMLResponse)
async def subtask_delete(subtask_id: int):
    with db_context() as conn:
        db.delete_subtask(conn, subtask_id)
    return HTMLResponse("")


@app.post("/tasks/{task_id}/subtasks/reorder", response_class=HTMLResponse)
async def subtask_reorder(task_id: int, order: str = Form(...)):
    ids = [int(x) for x in order.split(",") if x.strip()]
    with db_context() as conn:
        db.reorder_subtasks(conn, task_id, ids)
    return HTMLResponse("")


@app.post("/tasks/{task_id}/subtasks/generate", response_class=HTMLResponse)
async def subtask_generate(task_id: int, request: Request):
    with db_context() as conn:
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])
        goal = conn.execute(
            "SELECT g.* FROM goals g JOIN projects p ON p.goal_id = g.id WHERE p.id = ?",
            (project["id"],),
        ).fetchone()
        siblings = [
            dict(t) for t in db.fetch_tasks_for_project(conn, project["id"])
            if t["id"] != task_id
        ]

    result = ai.generate_subtask_breakdown(
        dict(task), dict(project), dict(goal), siblings
    )

    if not result:
        return HTMLResponse(
            '<div class="card" style="border-color:rgba(247,111,111,0.3); background:rgba(247,111,111,0.04); margin-bottom:1rem;">'
            '<p class="text-sm" style="color:var(--red);">AI failed to generate breakdown. Try again.</p></div>'
        )

    return templates.TemplateResponse(
        request,
        "partials/subtask_suggestions.html",
        {"task_id": task_id, "result": result},
    )


@app.post("/tasks/{task_id}/subtasks/from-template/{template_id}", response_class=HTMLResponse)
async def subtask_from_template(task_id: int, template_id: int, request: Request):
    with db_context() as conn:
        tpl = db.fetch_template(conn, template_id)
        if not tpl:
            return RedirectResponse(f"/tasks/{task_id}/plan", status_code=303)
        steps = json.loads(tpl["steps_json"])
        result = {
            "summary": tpl["description"],
            "subtasks": steps,
        }
    return templates.TemplateResponse(
        request,
        "partials/subtask_suggestions.html",
        {"task_id": task_id, "result": result},
    )


@app.post("/tasks/{task_id}/subtasks/apply", response_class=HTMLResponse)
async def subtask_apply(task_id: int, selected_json: str = Form(...)):
    data = json.loads(selected_json)
    with db_context() as conn:
        base_order = db.next_sort_order(conn, task_id)
        for i, item in enumerate(data):
            db.insert_subtask(
                conn, task_id,
                item["title"],
                item.get("description", ""),
                item.get("estimated_minutes", 15),
                base_order + i,
            )
    return RedirectResponse(f"/tasks/{task_id}/plan", status_code=303)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/end", response_class=HTMLResponse)
async def end_session(
    session_id: int,
    request: Request,
    notes: str = Form(""),
    focus: str = Form(""),
):
    with db_context() as conn:
        db.end_session(conn, session_id, notes or None)
        session_row = db.fetch_session(conn, session_id)
        task_id = session_row["task_id"]
        db.update_task_status(conn, task_id, "done")

    if focus:
        return RedirectResponse("/?completed=1", status_code=303)

    with db_context() as conn:
        task = db.fetch_task(conn, task_id)
        project = db.fetch_project(conn, task["project_id"])

    return templates.TemplateResponse(
        request,
        "partials/task_card.html",
        {"task": task, "project": project, "session": None},
    )
