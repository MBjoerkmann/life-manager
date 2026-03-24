"""AI-powered life coaching, daily planning, and weekly review.

Supports three providers — whichever key you set:
  ANTHROPIC_API_KEY  → Claude (claude-sonnet-4-6)
  OPENAI_API_KEY     → GPT-4o mini
  GEMINI_API_KEY     → Gemini 2.0 Flash (free tier available)

Override auto-detection with: AI_PROVIDER=anthropic|openai|gemini
Override model with: AI_MODEL=<model-id>
"""

import json
import logging
import os
import re
from datetime import date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

PROVIDER_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _detect_provider() -> str | None:
    explicit = os.environ.get("AI_PROVIDER", "").lower()
    if explicit in PROVIDER_MODELS:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def is_enabled() -> bool:
    return _detect_provider() is not None


def _call_ai(system: str, user_content: str, max_tokens: int = 2048) -> str:
    """Call the configured AI provider. Returns the response text."""
    provider = _detect_provider()
    if not provider:
        raise RuntimeError("No AI provider configured")

    model = os.environ.get("AI_MODEL") or PROVIDER_MODELS[provider]

    if provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text

    from openai import OpenAI
    if provider == "gemini":
        client = OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url=GEMINI_BASE_URL,
        )
    else:
        client = OpenAI()

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

DAILY_COACH_PROMPT = """\
You are a personal life coach and daily planner. You know the user's long-term \
goals, their open tasks, and how they've been performing recently. Your job is \
to plan their day and keep them motivated.

You will receive their goals, open tasks, performance stats (completion rate, \
streak, neglected goals), and yesterday's results.

Respond ONLY with valid JSON in this exact format:
{
  "greeting": "A warm 2-3 sentence morning message. Reference yesterday's results \
(celebrate wins, acknowledge rest days gently). Connect today to their bigger vision.",
  "coaching_note": "A specific observation about their patterns — neglected goals, \
streak status, or workload. Suggest an adjustment if needed. Set to null if nothing notable.",
  "schedule": [
    {
      "task_id": <int>,
      "time_block": "morning|midday|afternoon|evening",
      "reason": "Why this task, why this time slot — connect it to their goals."
    }
  ],
  "motivation": "One sentence tied to their streak, progress, or a specific goal. \
Not generic — reference something concrete."
}

Guidelines:
- Select 3-5 tasks for a realistic day (90-180 min total)
- Put focused/creative tasks in morning, lighter tasks in afternoon
- If a goal has been neglected (>7 days no activity), include at least one task from it
- If completion rate is low (<60%), suggest fewer tasks and acknowledge the struggle
- If streak is high (>3 days), celebrate it
- If they skipped yesterday entirely, be encouraging but gentle — suggest just 1-2 tasks
- Only select tasks from the provided list — never invent IDs
- time_block order: morning → midday → afternoon → evening"""

WEEK_PLAN_PROMPT = """\
You are a life coach planning the user's week. Distribute their open tasks \
across the upcoming days to create a balanced, achievable week.

Respond ONLY with valid JSON in this exact format:
{
  "overview": "2-3 sentences: the week's strategy and focus areas.",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "theme": "Brief theme for the day (2-4 words)",
      "tasks": [
        {
          "task_id": <int>,
          "time_block": "morning|midday|afternoon",
          "reason": "Why this task on this day."
        }
      ]
    }
  ]
}

Guidelines:
- Plan 5-7 days starting from the given start date
- Max 3-5 tasks per day, max ~180 min per day
- Include at least one lighter day or rest day (0-1 tasks)
- Front-load the week with harder tasks (Mon-Wed), lighter Thu-Fri
- Spread tasks across goals — don't stack one goal on one day
- Consider deadlines: tasks in projects with approaching deadlines go earlier
- Only select tasks from the provided list — never invent IDs
- It's fine to leave some tasks unscheduled if there are too many"""

ROADMAP_SYSTEM_PROMPT = """\
You are an expert life coach and project planner. Given a user's long-term goal, \
create a concrete, actionable roadmap broken into phases. Each phase has projects, \
each project has specific tasks.

Respond ONLY with valid JSON in this exact format:
{
  "roadmap_summary": "2-3 sentences: the overall journey and why this approach makes sense.",
  "phases": [
    {
      "name": "Phase 1: Descriptive Name",
      "duration": "e.g., 2-4 weeks",
      "description": "What this phase achieves and why it comes first.",
      "projects": [
        {
          "title": "Project title",
          "description": "What completing this project accomplishes.",
          "priority": 4,
          "tasks": [
            {
              "title": "Specific, actionable task (verb + object)",
              "description": "Brief context or approach for this task.",
              "estimated_minutes": 60
            }
          ]
        }
      ]
    }
  ]
}

Guidelines:
- Create 2-4 phases that build logically on each other (foundation → practice → mastery)
- Each phase: 1-3 projects
- Each project: 3-6 tasks
- Tasks must be completable in one sitting (30-120 min), start with a verb
- Priorities: 5=critical, 4=high, 3=medium, 2=low, 1=someday
- Do not duplicate any existing projects the user already has
- Be specific to their actual goal — no generic advice
- estimated_minutes must be an integer"""

SUBTASK_BREAKDOWN_PROMPT = """\
You are an expert task planner. Given a task within a project and goal context, \
break it down into clear, ordered subtasks (steps to complete it).

Respond ONLY with valid JSON in this exact format:
{
  "summary": "Brief explanation of the approach and why these steps make sense.",
  "subtasks": [
    {
      "title": "Specific, actionable step (verb + object)",
      "description": "Brief context, tips, or what 'done' looks like for this step.",
      "estimated_minutes": 15
    }
  ]
}

Guidelines:
- Create 4-10 subtasks depending on task complexity
- Each subtask should be completable in one sitting (5-60 min)
- Start each title with an action verb
- Order matters: steps should flow logically (prerequisites first)
- Be specific to the actual task — no generic filler steps
- Include any research, preparation, or review steps that are often forgotten
- estimated_minutes must be an integer between 5 and 60
- The sum of all subtask minutes should roughly match the parent task's estimated time \
(but can exceed it if the task was underestimated)"""

REVIEW_SYSTEM_PROMPT = """\
You are a thoughtful life coach reviewing the user's week. Write a brief, \
honest weekly review in plain text (no markdown headers, just paragraphs).

Cover:
1. What was accomplished, grouped by goal
2. Where momentum is strong
3. Goals or projects that got no attention this week
4. One concrete suggestion for next week

Be direct and specific — reference their actual goals and tasks. \
Keep it under 200 words."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_daily_briefing(
    hierarchy: list[dict],
    recent_completions: list[dict],
    performance: dict,
    today: date,
) -> dict | None:
    """Returns structured day plan or None."""
    if not is_enabled():
        return None

    has_tasks = any(
        t
        for item in hierarchy
        for p in item["projects"]
        for t in p["tasks"]
    )
    if not has_tasks:
        return None

    try:
        context = _build_daily_context(hierarchy, recent_completions, performance, today)
        text = _call_ai(DAILY_COACH_PROMPT, context, max_tokens=1500)
        return _parse_json_response(text)
    except Exception:
        logger.exception("Failed to generate daily briefing")
        return None


def generate_week_plan(
    hierarchy: list[dict],
    performance: dict,
    week_start: date,
    days: int = 7,
) -> dict | None:
    """Returns week plan dict or None."""
    if not is_enabled():
        return None

    has_tasks = any(
        t
        for item in hierarchy
        for p in item["projects"]
        for t in p["tasks"]
    )
    if not has_tasks:
        return None

    try:
        context = _build_week_context(hierarchy, performance, week_start, days)
        text = _call_ai(WEEK_PLAN_PROMPT, context, max_tokens=3000)
        return _parse_json_response(text)
    except Exception:
        logger.exception("Failed to generate week plan")
        return None


def generate_goal_roadmap(goal: dict, existing_projects: list[dict]) -> dict | None:
    """Returns a phased roadmap dict or None."""
    if not is_enabled():
        return None
    try:
        context = _build_roadmap_context(goal, existing_projects)
        text = _call_ai(ROADMAP_SYSTEM_PROMPT, context, max_tokens=4096)
        return _parse_json_response(text)
    except Exception:
        logger.exception("Failed to generate goal roadmap")
        return None


def generate_subtask_breakdown(
    task: dict,
    project: dict,
    goal: dict,
    sibling_tasks: list[dict],
) -> dict | None:
    """Returns subtask breakdown dict or None."""
    if not is_enabled():
        return None
    try:
        context = _build_subtask_context(task, project, goal, sibling_tasks)
        text = _call_ai(SUBTASK_BREAKDOWN_PROMPT, context, max_tokens=2048)
        return _parse_json_response(text)
    except Exception:
        logger.exception("Failed to generate subtask breakdown")
        return None


def generate_weekly_review(
    hierarchy: list[dict],
    completions: list[dict],
    sessions_summary: dict,
    today: date,
) -> str | None:
    """Returns plain-text review or None."""
    if not is_enabled():
        return None
    try:
        context = _build_review_context(hierarchy, completions, sessions_summary, today)
        return _call_ai(REVIEW_SYSTEM_PROMPT, context, max_tokens=1500)
    except Exception:
        logger.exception("Failed to generate weekly review")
        return None


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _build_daily_context(hierarchy, recent_completions, performance, today):
    lines = [f"Today is {today.strftime('%A, %B %d, %Y')}.\n"]

    # Performance stats for coaching
    lines.append("## Your Recent Performance\n")
    lines.append(f"- Completion rate (14 days): {performance['completion_rate']}%")
    lines.append(f"- Current streak: {performance['streak']} day(s)")
    yd = performance["yesterday_done"]
    yt = performance["yesterday_total"]
    if yt > 0:
        lines.append(f"- Yesterday: completed {yd} of {yt} tasks")
    else:
        lines.append("- Yesterday: no tasks were scheduled")
    if performance["neglected_goals"]:
        lines.append(f"- Neglected goals (no activity in 7 days): {', '.join(performance['neglected_goals'])}")
    lines.append("")

    lines.append("## Goals & Open Tasks\n")
    for item in hierarchy:
        g = item["goal"]
        lines.append(f"### Goal: {g['title']} (priority {g['priority']}/5)")
        if g.get("description"):
            lines.append(f"  {g['description']}")
        for pitem in item["projects"]:
            p = pitem["project"]
            deadline = f" — deadline: {p['deadline']}" if p.get("deadline") else ""
            lines.append(
                f"  Project: {p['title']} (priority {p['priority']}/5{deadline})"
            )
            for t in pitem["tasks"]:
                lines.append(
                    f"    - Task #{t['id']}: {t['title']} "
                    f"({t['estimated_minutes']}min, status: {t['status']})"
                )

    if recent_completions:
        lines.append("\n## Completed recently\n")
        for c in recent_completions[:10]:
            lines.append(
                f"- {c['title']} (project: {c['project_title']}, "
                f"goal: {c['goal_title']})"
            )

    return "\n".join(lines)


def _build_week_context(hierarchy, performance, week_start, days):
    from datetime import timedelta
    dates = [(week_start + timedelta(days=i)) for i in range(days)]

    lines = [f"Plan for the week of {week_start.strftime('%B %d, %Y')}"]
    lines.append(f"Days to plan: {', '.join(d.strftime('%A %b %d') for d in dates)}\n")

    lines.append("## Performance context\n")
    lines.append(f"- Completion rate: {performance['completion_rate']}%")
    lines.append(f"- Streak: {performance['streak']} days")
    if performance["neglected_goals"]:
        lines.append(f"- Neglected goals: {', '.join(performance['neglected_goals'])}")
    lines.append("")

    lines.append("## Goals & Open Tasks\n")
    for item in hierarchy:
        g = item["goal"]
        lines.append(f"### Goal: {g['title']} (priority {g['priority']}/5)")
        for pitem in item["projects"]:
            p = pitem["project"]
            deadline = f" — deadline: {p['deadline']}" if p.get("deadline") else ""
            lines.append(
                f"  Project: {p['title']} (priority {p['priority']}/5{deadline})"
            )
            for t in pitem["tasks"]:
                lines.append(
                    f"    - Task #{t['id']}: {t['title']} "
                    f"({t['estimated_minutes']}min)"
                )

    return "\n".join(lines)


def _build_roadmap_context(goal, existing_projects):
    lines = [f"Goal: {goal['title']}"]
    if goal.get("description"):
        lines.append(f"Description: {goal['description']}")
    lines.append(f"Priority: {goal['priority']}/5\n")
    if existing_projects:
        lines.append("Existing projects (do not duplicate):")
        for p in existing_projects:
            lines.append(f"  - {p['title']}")
    return "\n".join(lines)


def _build_subtask_context(task, project, goal, sibling_tasks):
    lines = [f"Task: {task['title']}"]
    if task.get("description"):
        lines.append(f"Description: {task['description']}")
    lines.append(f"Estimated time: {task['estimated_minutes']} minutes")
    lines.append(f"\nProject: {project['title']}")
    if project.get("description"):
        lines.append(f"Project description: {project['description']}")
    lines.append(f"\nGoal: {goal['title']}")
    if goal.get("description"):
        lines.append(f"Goal description: {goal['description']}")
    if sibling_tasks:
        lines.append("\nOther tasks in this project (for scope context):")
        for t in sibling_tasks:
            lines.append(f"  - {t['title']} ({t['status']})")
    return "\n".join(lines)


def _build_review_context(hierarchy, completions, sessions_summary, today):
    lines = [f"Week ending {today.strftime('%A, %B %d, %Y')}.\n"]
    lines.append("## Goals\n")
    for item in hierarchy:
        g = item["goal"]
        lines.append(f"- {g['title']} (priority {g['priority']}/5)")
    lines.append("\n## Completed tasks this week\n")
    if completions:
        for c in completions:
            lines.append(
                f"- {c['title']} (project: {c['project_title']}, "
                f"goal: {c['goal_title']})"
            )
    else:
        lines.append("No tasks completed this week.")
    count = sessions_summary["count"]
    mins = sessions_summary["total_minutes"]
    lines.append(f"\n## Sessions: {count} sessions, {mins} minutes total")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict | None:
    """Extract JSON from model response, handling markdown code blocks."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None
