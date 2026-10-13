"""Structured-output schemas for extraction (specs/05 §A)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Source(BaseModel):
    quote: str
    location: str                 # e.g. "role 2, bullet 3"


class Bullet(BaseModel):
    text: str
    source: Source


class Role(BaseModel):
    title: str
    employer: str | None
    industry: str | None
    start: str | None             # YYYY-MM or YYYY
    end: str | None
    is_current: bool
    team_size: int | None
    bullets: list[Bullet]
    source: Source


class Project(BaseModel):
    kind: Literal["project", "hackathon", "publication", "case_study", "presentation", "work_sample"]
    name: str
    description: str | None
    url: str | None
    start: str | None
    end: str | None
    role_index: int | None        # index into roles if done within a role
    bullets: list[Bullet]
    source: Source


class Claim(BaseModel):
    kind: Literal["skill", "tool", "award"]
    name: str
    bullet_ref: str | None        # location of the bullet it appears in; null = listed only
    source: Source


class Certification(BaseModel):
    name: str
    issuer: str | None
    date: str | None
    credential_id: str | None
    verify_url: str | None
    source: Source


class ProfileExtract(BaseModel):
    """Resume and LinkedIn PDF."""
    roles: list[Role]
    projects: list[Project]
    claims: list[Claim]
    certifications: list[Certification]


class PortfolioExtract(BaseModel):
    """Portfolio page and GitHub README."""
    projects: list[Project]
    claims: list[Claim]
