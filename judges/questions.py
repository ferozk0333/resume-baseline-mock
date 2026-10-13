"""Judgment catalogue (specs/05 §B): every question a judge can be asked.

Options are ordered best → worst. Their names must match the label maps in
scoring/formulas.py (a test checks this). Descriptions double as the rubric
sent to the model (Claude prompt text, Jev `criteria`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["choice", "yes_no", "graded"]


@dataclass(frozen=True)
class Question:
    key: str
    kind: Kind                      # graded = ordered levels (Jev Score); choice/yes_no as named
    instructions: str
    options: dict[str, str]         # option → description, best first; yes_no uses {"yes", "no"}
    job_dependent: bool             # True → stored with baseline_id + baseline_hash
    reason_first: bool = False      # model writes reasoning before the label
    none_option: str | None = None  # option that needs no supporting quote


YES_NO = {"yes": "The text clearly shows it.", "no": "The text does not show it, or it is unclear."}

_QUALITY_CHECKS = {
    1: "Does the item name the specific tools, technologies or methods used?",
    2: "Does the item include at least one number (metric, size, count, percentage, money, time)?",
    3: "Does the item include dates or a timeframe?",
    4: "Does the item describe the context (the problem, product, team or organization)?",
    5: "Does the item state the candidate's own role or contribution?",
}

_LIST = [
    Question(
        "relevance", "choice",
        "How relevant is this role or project to the target role in <baseline>?",
        {
            "relevant": "The work is substantially the same kind of work as the target role.",
            "adjacent": "Related work that builds transferable skills for the target role, but a different job.",
            "unrelated": "Not related to the target role.",
        },
        job_dependent=True, none_option="unrelated",
    ),
    Question(
        "scope_vs_baseline", "graded",
        "Compare the responsibility shown in this role with the scope expected in <baseline>.",
        {
            "meets": "Shows the expected scope of responsibility or more.",
            "partly": "Shows part of the expected scope.",
            "below": "Shows less responsibility than expected, or scope is not stated.",
        },
        job_dependent=True, none_option="below",
    ),
    Question(
        "progression", "graded",
        "Have responsibilities become more impactful in the most recent role compared with previous ones? "
        "Roles are listed oldest first.",
        {
            "more_impactful": "The most recent role carries clearly more responsibility or impact.",
            "same": "About the same level of responsibility, or it cannot be told.",
            "less": "The most recent role carries less responsibility.",
        },
        job_dependent=False, none_option="same",
    ),
    Question(
        "skill_evidence", "choice",
        "How does the candidate's text evidence the skill in <criterion>?",
        {
            "bullet": "The skill is shown inside a role or project bullet or case study.",
            "keyword": "The skill is only named, e.g. in a skills list.",
            "missing": "The skill does not appear.",
        },
        job_dependent=True, none_option="missing",
    ),
    Question(
        "applied_level", "choice",
        "How fully does the candidate's text show the skill in <criterion> being applied?",
        {
            "skill_task_result": "Shows the skill applied to a task AND the result of that task.",
            "skill_task": "Shows the skill applied to a task, without a result.",
            "mention_or_missing": "Only mentions the skill, or does not show it.",
        },
        job_dependent=True, none_option="mention_or_missing",
    ),
    Question(
        "tool_evidence", "choice",
        "How does the candidate's text evidence the tool in <criterion>?",
        {
            "used": "The tool is used in a role, project, work sample or repository.",
            "certified": "The candidate holds a certification for the tool, with no usage shown.",
            "listed": "The tool is only listed.",
            "missing": "The tool does not appear.",
        },
        job_dependent=True, none_option="missing",
    ),
    Question(
        "complexity", "graded",
        "Judge whether this item shows the desired level of complexity in <baseline> "
        "(problem difficulty, scale, constraints, ambiguity). Reason first.",
        {
            "meets": "Shows the desired complexity.",
            "partly": "Shows some of the desired complexity.",
            "no": "Does not show the desired complexity.",
        },
        job_dependent=True, reason_first=True, none_option="no",
    ),
    Question("work_sample_relevant", "yes_no",
             "Is this work sample relevant to the target role in <baseline>?", YES_NO, job_dependent=True,
             none_option="no"),
    Question("outcome_metric", "yes_no",
             "Does the bullet state a measurable result (a number, percentage, amount or count)?", YES_NO,
             job_dependent=False, none_option="no"),
    Question("outcome_direction", "yes_no",
             "Does the bullet state the direction of change (e.g. increased, reduced, improved, cut)?", YES_NO,
             job_dependent=False, none_option="no"),
    Question(
        "impact_type", "choice",
        "What kind of impact does the bullet describe?",
        {
            "business": "Business impact: revenue, cost, savings, retention, customers, churn, conversion.",
            "operational": "Operational impact: backlog, report time, runtime, process speed.",
            "none": "No impact is described.",
        },
        job_dependent=False, none_option="none",
    ),
    Question(
        "ownership", "choice",
        "What was the candidate's own role in the work described by the bullet?",
        {
            "led": "The candidate led, owned or drove the work.",
            "contributed": "The candidate contributed to or supported the work.",
            "not_stated": "The candidate's role is not stated.",
        },
        job_dependent=False, none_option="not_stated",
    ),
    Question(
        "reach", "choice",
        "Who did the work described by the bullet reach?",
        {
            "external": "People outside the organization: customers, users, the public.",
            "organization": "The whole organization or a large part of it.",
            "cross_functional": "Other teams or functions.",
            "team": "The candidate's own team.",
            "own_work": "Only the candidate's own work.",
            "not_stated": "Reach is not stated.",
        },
        job_dependent=False, none_option="not_stated",
    ),
    Question("award_relevant", "yes_no",
             "Is this a professional award relevant to the target role in <baseline>?", YES_NO,
             job_dependent=True, none_option="no"),
    Question("publication_relevant", "yes_no",
             "Is this publication in a field relevant to the target role in <baseline>?", YES_NO,
             job_dependent=True, none_option="no"),
    Question(
        "proof_support", "choice",
        "What is the strongest support for this claim in the candidate's evidence?",
        {
            "artifact": "A separate artifact shows it: repository, case study, dashboard or certificate.",
            "bullet": "A role or project bullet shows it.",
            "claimed_only": "It is only claimed.",
        },
        job_dependent=False, none_option="claimed_only",
    ),
    Question(
        "reference_confirms", "choice",
        "Does the reference statement confirm that the candidate did work relevant to <baseline>?",
        {
            "confirms_relevant_work": "The statement confirms relevant work.",
            "does_not": "The statement does not confirm relevant work.",
        },
        job_dependent=True, none_option="does_not",
    ),
    Question(
        "source_alignment", "choice",
        "Does this source align with the resume (skills, projects, experience, dates)?",
        {
            "aligned": "The source is consistent with the resume.",
            "not_aligned": "The source contradicts the resume or shares nothing with it.",
        },
        job_dependent=False, none_option="not_aligned",
    ),
    *[
        Question(f"quality_check_{i}", "yes_no", text, YES_NO, job_dependent=False, none_option="no")
        for i, text in _QUALITY_CHECKS.items()
    ],
]

QUESTIONS: dict[str, Question] = {q.key: q for q in _LIST}
