"""Pydantic schemas for the CPM engine — Reasoning Engine Plan 1."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class DependencyType(str, Enum):
    FS = "FS"  # finish-to-start (default)
    SS = "SS"  # start-to-start
    FF = "FF"  # finish-to-finish
    SF = "SF"  # start-to-finish


class Dependency(BaseModel):
    predecessor_id: str
    type: DependencyType = DependencyType.FS
    lag: int = 0  # working days; may be negative


class WorkCalendar(BaseModel):
    """A working-day calendar. Monday=0 .. Sunday=6.

    The nth_working_day() method is added in Task 2.
    """
    work_weekdays: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    holidays: List[date] = Field(default_factory=list)


class Activity(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""
    duration: int = Field(ge=0)  # working days
    predecessors: List[Dependency] = Field(default_factory=list)
    wbs_code: str = ""


class CPMResult(BaseModel):
    id: str
    name: str
    duration: int
    early_start_day: int
    early_finish_day: int
    late_start_day: int
    late_finish_day: int
    total_float: int
    free_float: int
    is_critical: bool
    early_start: Optional[date] = None
    early_finish: Optional[date] = None
    late_start: Optional[date] = None
    late_finish: Optional[date] = None


class CPMInput(BaseModel):
    activities: List[Activity]
    project_start: Optional[date] = None
    calendar: WorkCalendar = Field(default_factory=WorkCalendar)


class CPMOutput(BaseModel):
    results: List[CPMResult]
    project_duration: int
    project_finish: Optional[date] = None
    critical_path: List[str]
    critical_percentage: float
    near_critical: List[str]
