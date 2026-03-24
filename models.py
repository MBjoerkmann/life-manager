from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Goal:
    id: int
    title: str
    description: Optional[str]
    priority: int
    created_at: str
    active: bool
    projects: list = field(default_factory=list)


@dataclass
class Project:
    id: int
    goal_id: int
    title: str
    description: Optional[str]
    deadline: Optional[str]
    priority: int
    active: bool
    created_at: str
    tasks: list = field(default_factory=list)


@dataclass
class Task:
    id: int
    project_id: int
    title: str
    description: Optional[str]
    estimated_minutes: int
    status: str
    scheduled_date: Optional[str]
    created_at: str
    # Denormalised fields populated at query time
    project_title: Optional[str] = None
    goal_title: Optional[str] = None
    active_session_id: Optional[int] = None
    active_session_started_at: Optional[str] = None


@dataclass
class Session:
    id: int
    task_id: int
    started_at: str
    ended_at: Optional[str]
    notes: Optional[str]
