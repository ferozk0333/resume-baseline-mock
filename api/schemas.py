"""Request and response models of the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateIn(BaseModel):
    name: str | None = None
    target_role: str | None = None


class CandidateOut(BaseModel):
    id: str
    name: str | None
    target_role: str | None
    github_username: str | None = None
    portfolio_url: str | None = None
    created_at: str | None = None


class TaskOut(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    id: str
    kind: str
    status: str
    step: str | None
    message: str | None
    percent: float | None
    error: str | None
    candidate_id: str | None
    run_id: str | None
    result: dict | None


class ScoreIn(BaseModel):
    baseline_id: str
    weights: dict | None = None


class WeightsIn(BaseModel):
    weights: dict


class WeightSuggestIn(BaseModel):
    text: str
    fresh: bool = False             # "Suggest again": bypass the cache


class PostingUrlIn(BaseModel):
    url: str


class JobUpdate(BaseModel):
    weights: dict | None = None
    must_haves: list[str] | None = None
    preset: str | None = None


class StatusIn(BaseModel):
    status: Literal["new", "shortlisted", "not_moving_forward"]


class StageMove(BaseModel):
    stage_id: str | None


class StageIn(BaseModel):
    name: str
    focus: list[str] | None = None


class StageEdit(BaseModel):
    name: str | None = None
    focus: list[str] | None = None


class StageOrder(BaseModel):
    ids: list[str]


class JobSettingsIn(BaseModel):
    recruiter_name: str | None = None
    duration_minutes: int | None = None
    availability: str | None = None
    scheduling_link: str | None = None


class RejectIn(BaseModel):
    reason_ids: list[str] | None = None
    note: str | None = None


class JobDraftIn(BaseModel):
    draft: dict


class CopyIn(BaseModel):
    kind: Literal["job", "template"]
    id: str


class SaveJobIn(BaseModel):
    confirm: bool = False                # "N applicants will be re-scored" was confirmed


class JobStatusIn(BaseModel):
    status: Literal["open", "closed"]


class ApplyIn(BaseModel):
    job_id: str


class RecomputeIn(BaseModel):
    test: bool | None = None             # None: the model only when a posting needs reading; True/False forces it


class DecideIn(BaseModel):
    approve: bool
    canonical: str | None = None


class SourceIn(BaseModel):
    source: Literal["manual", "market"]
    confirm: bool = False


class SwitchBaselineIn(BaseModel):
    source: Literal["standard", "market"]
    confirm: bool = False                # the re-score notice was confirmed


class TargetsIn(BaseModel):
    baseline_ids: list[str]
    primary: str | None = None


class RetryRolesIn(BaseModel):
    baseline_ids: list[str] | None = None


class PlanDoneIn(BaseModel):
    baseline_id: str
    title: str
    done: bool = True


class TargetIn(BaseModel):
    baseline_id: str


class OnboardingStepIn(BaseModel):
    step: Literal["proof", "roles", "review", "scores", "details", "done"]


class SaveJobPrefIn(BaseModel):
    saved: bool


class HideJobIn(BaseModel):
    reason: Literal["location", "salary", "role", "seniority", "other"] | None = None


class WorkflowStart(BaseModel):
    confirm_move: bool = False           # "Move to Shortlisted and start?" was confirmed


class BulkStart(WorkflowStart):
    application_ids: list[str]
    check_only: bool = False             # only list who would be moved to Shortlisted (for the confirmation)


class WorkflowAction(BaseModel):
    """The recruiter's answer at the current step, e.g. {"action": "send", "email_id": …},
    {"action": "confirm", "start": "2026-10-20T11:00:00-04:00"}, {"action": "choose", "option": "slots"}."""
    action: str
    email_id: str | None = None
    message_id: str | None = None
    option: str | None = None
    start: str | None = None
    end: str | None = None
    stage_id: str | None = None


class ReplyIn(BaseModel):
    text: str | None = None
    canned: str | None = None


class Rating(BaseModel):
    area: str
    rating: int
    notes: str | None = None


class ScorecardIn(BaseModel):
    interviewer: str
    ratings: list[Rating]
    notes: str | None = None
    recommendation: Literal["strong_yes", "yes", "no", "strong_no"]


class StageEmailIn(BaseModel):
    stage_id: str


class EmailEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


class ProfileEdit(BaseModel):
    """One review edit. `item` is role | project | bullet? (bullets go with their role/project) | claim |
    certification | reference (name, relationship, contact, statement; never verified) | extra (a display-only proof
    type); `op` add | edit | delete | add_back (add_back uses `id` of a
    dropped item, or of a mention: an item the candidate deleted that a later source named again) | undo (item
    "deleted", `id` from a delete's response) | confirm_dates ("the months are right": clears the date flags)."""
    op: Literal["add", "edit", "delete", "add_back", "undo", "confirm_dates"]
    item: Literal["role", "project", "claim", "certification", "reference", "dropped", "mention", "extra", "deleted"]
    id: str | None = None
    data: dict = Field(default_factory=dict)
