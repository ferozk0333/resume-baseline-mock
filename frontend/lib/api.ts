import { useEffect, useState } from "react";

// Typed client for the FastAPI backend (specs/10 §2).
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    throw new ApiError(res.status, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const get = <T>(path: string) => request<T>(path);
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
export const put = <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => request<void>(path, { method: "DELETE" });

// --- Types -----------------------------------------------------------------------------------------
export type Criterion = { id?: string | null; kind: "skill" | "tool" | "certification"; name: string;
  description?: string | null; aliases: string[]; priority: "required" | "preferred" };
export type Baseline = { id?: string | null; title: string; level: string; required_years: number;
  expected_reach: number; scope_expectation?: string | null; complexity_criteria?: string | null;
  description?: string | null; is_mock: boolean; updated_at?: string | null; criteria: Criterion[] };
export type Candidate = { id: string; name: string | null; target_role: string | null; created_at?: string };
export type Task = { id: string; kind: string; status: "running" | "done" | "error"; step: string | null;
  message: string | null; percent: number | null; error: string | null; run_id: string | null;
  result: Record<string, unknown> | null };
export type TaskEvent = { seq: number; step: string; message: string; percent: number | null; status: string };

export type Bullet = { id: string; text: string; source: string };
/** Review fields (specs/19): `sources` are chips ("Resume", "LinkedIn", "Added by you"); `review_codes` the raw
 * date flags; `year_only` and `conflict` feed "Needs your attention". */
type ReviewItem = { review_reasons: string[]; review_codes?: string[]; year_only?: boolean; conflict?: string | null;
  sources?: string[] };
export type Role = ReviewItem & { id: string; title: string; employer: string | null; start_date: string | null;
  end_date: string | null; is_current: boolean; bullets: Bullet[]; quote: string | null;
  source: string; industry: string | null; team_size: number | null; location?: string | null };
export type Project = ReviewItem & { id: string; kind: string; name: string; description: string | null; url: string | null;
  start_date: string | null; end_date: string | null; bullets: Bullet[];
  quote: string | null; is_fork: boolean | null; own_commits: number | null; source: string; role_id?: string | null;
  details?: Record<string, string> };
export type Claim = { id: string; kind: string; name_raw: string; bullet_id: string | null; source: string;
  quote: string | null; group?: string; sources?: string[]; details?: Record<string, string> };
export type Certification = { id: string; name: string; issuer: string | null; credential_id: string | null;
  verify_url: string | null; source: string; issued_date?: string | null; sources?: string[];
  exam_code?: string | null; credential_status?: string | null };
/** A reference the candidate typed (form or Review). Never verified (spec 20). */
export type Reference = { id: string; name: string; relationship: string | null; contact: string | null;
  statement: string | null };
/** Display-only proof types (specs/19 Phase D): shown to recruiters, never scored. */
export type ExtraItem = { id: string; type: "education" | "patent" | "volunteering" | "course" | "language";
  fields: Record<string, string>; source: string; quote: string | null; sources: string[] };
export type Dropped = { id: string; item_type: string; text: string; reason: string };
export type MediaLink = { id: string; url: string; kind: "youtube" | "loom"; title: string | null; embed_url: string };
export type ProfileResponse = {
  candidate: Candidate;
  profile: { roles: Role[]; projects: Project[]; claims: Claim[]; certifications: Certification[];
    references?: Reference[] };
  dropped: Dropped[]; warnings: string[]; blocker: string | null; media_links: MediaLink[];
  sources: { id: string; type: string; status: string; text: string | null }[];
  /** Sources still being read while Review is open (specs/18 Phase 3); Confirm waits for them. */
  pending?: { type: string; label: string }[]; reading?: boolean;
  /** Deleted in Review, then named again by a source read later: never re-added, offered with "Add back". */
  mentions?: { id: string; item_type: string; label: string; source: string }[];
  /** "We ignored hidden text in your resume." (hidden text the injection screen also flagged). */
  notices?: string[];
  extra?: ExtraItem[]; skill_groups?: string[];
  /** After a delete: the id that "Undo" sends back. */
  undo?: string | null;
};

export type Judgment = { question: string; subject_type: string; subject: string; label: string | null;
  method: string | null; model_version: string | null; quote: string | null; confidence: number | null;
  reasoning: string | null };
export type Subcategory = { key: string; label: string; dimension: string;
  status: "scored" | "missing" | "na" | "error" | "pending";
  score: number | null; guidance: string | null; formula: string | null; weight: number; effective_weight: number;
  details: Record<string, unknown> & { judgments?: Judgment[] } };
export type Gap = { subcategory: string; label: string; dimension: string; status: string; score: number;
  gain: number; gain_text: string; guidance: string | null };
export type Weights = { dimensions: Record<string, number>; subcategories: Record<string, Record<string, number>> };
export type RunView = { run: { id: string; candidate_id: string; baseline_id: string; plan_status: string | null;
    status?: string }; progress?: { done: number; total: number };
  weights: Weights; final: number | null; dimensions: Record<string, number | null>; subcategories: Subcategory[];
  gaps: Gap[]; audit?: boolean };

// --- Computation (audit round, item 1; SCORE_AUDIT): read-only, from the saved data the score used ------------------
export type Decided = { question?: string; label: string | null; decided_by: string; confidence?: number | null;
  fallback?: { from: string; label: string | null; confidence: number | null } | null; route?: string; reason?: string };
export type CompInput = { kind: string; text: string; label: string | null; value: number | null; relevance: number | null;
  factors: Record<string, unknown>; decided_by: Decided[]; source: string | null; reason?: string };
export type SubComputation = { dimension: string; status: string; score: number | null;
  recomputed: { status: string; score: number | null }; matches_saved: boolean; status_reason: string;
  inputs: CompInput[]; excluded: CompInput[]; arithmetic: string[] };
export type DimComputation = { terms: { subcategory: string; label: string; weight: number; score: number; missing: boolean }[];
  excluded: { subcategory: string; label: string; weight: number; reason: string }[]; arithmetic: string; value: number | null };
export type Computation = { run_id: string; logic_version: string; today: string;
  subcategories: Record<string, SubComputation>; dimensions: Record<string, DimComputation>;
  final: { value: number | null; arithmetic: string; weights: Record<string, number>; excluded: string[] } };
export type PlanAction = { type: string; type_label: string; title: string; what_to_do: string; must_show: string[]; addresses: string[];
  covers_criteria: string[]; builds_on: string | null; points: number; points_text: string };
export type Plan = { status: "ready" | "generating" | "failed" | "none" | "deferred"; error?: string | null; actions: PlanAction[];
  quick_wins?: string[]; not_actionable?: string[]; gaps: Gap[] };

export const DIMENSIONS = ["experience", "expertise", "authority", "trust"] as const;
export const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// --- Recruiter (phase 11) ------------------------------------------------------------------------------
export type Job = { id: string; baseline_id: string; title: string; company: string; weights: Weights;
  must_haves: string[]; preset: string | null; applicants?: number; baseline?: Baseline; presets?: string[];
  presets_note?: string };
export type ApplicantRow = { application_id: string; candidate_id: string; name: string | null; current_role: string | null;
  status: "new" | "shortlisted" | "in_loop" | "not_moving_forward"; run_id: string | null; scored: boolean;
  match: number | null; confidence: number | null; must_haves_met: number; must_haves_total: number; why: string | null;
  tags: string[]; demo: string | null; percentile?: number | null; percentile_n?: number | null;
  stage_id?: string | null; stage?: string | null };
export type MustHave = { criterion_id: string; name: string; kind: string;
  status: "met_backed" | "met_on_paper" | "not_met" | "unknown" };
export type Requirement = { criterion_id: string; name: string; kind: string; priority: string; must_have: boolean;
  label: string | null; status: MustHave["status"]; quote: string | null; backing: string; source: string | null;
  link: string | null; evidence_id: string | null };
export type Repo = { id: string; name: string; description: string | null; url: string | null; languages: string;
  own_commits: number | null; fork: boolean; counted: boolean; last_update: string | null; readme_excerpt: string | null };
export type Panel = ApplicantRow & {
  job: { id: string; title: string; company: string };
  must_haves: MustHave[]; why_points: { text: string; evidence_ids: string[] }[];
  questions: { question: string; topic: string; reason: string }[]; generated_label: string | null;
  requirements: Requirement[]; dimensions: Record<string, number | null> | null;
  audit: { run_id: string; weights: Weights } | null;
  subcategories: { key: string; label: string; dimension: string; status: string; score: number | null;
    effective_weight: number; guidance: string | null;
    evidence: { question: string; label: string; text: string | null; supported: boolean }[] }[];
  differentiators: { tags: string[]; evidence: { name: string; quote: string | null; source: string | null;
    link: string | null; backing: string; evidence_id: string | null }[] };
  work: { videos: MediaLink[]; repos: Repo[]; portfolio: { name: string; description: string | null; url: string | null }[];
    portfolio_url: string | null; linkedin_uploaded: boolean; linkedin_file: boolean;
    resume_file: boolean; resume_file_kind: string | null; linkedin_link: { url: string; source: string } | null;
    certifications?: { name: string; issuer: string | null; credential_status: string | null; sources: string[] }[];
    /** Display-only proof (specs/19 Phase D): shown to recruiters, not scored. */
    more?: { type: string; label: string; fields: Record<string, string> }[] };
};
export type CompareColumn = { application_id: string; name: string | null; match: number | null; confidence: number | null;
  dimensions: Record<string, number | null> | null; must_haves_met: number; must_haves_total: number; scored: boolean;
  demo: string | null; top_evidence: Requirement[] };

// --- Benchmarks (phase 12) -------------------------------------------------------------------------------
export type Pct = { percentile: number; n: number; median: number; value: number } | null;
export type Benchmarks = {
  baseline_title: string | null;
  percentiles: { shown: boolean; reason?: string; pool_size: number; min_pool: number; demo_pool: boolean;
    final?: Pct; dimensions?: Record<string, Pct> };
  peer_signals: { min_group: number; demo_pool: boolean; dimensions: Record<string, { suppressed: boolean;
    group_size: number | null; signals: { feature: string; subcategory: string; rate: number; text: string }[] }> };
};

// --- Recruiter workflows (specs/13) -----------------------------------------------------------------------
export type Stage = { id: string; name: string; position: number; terminal: boolean; focus: string[]; applicants: number };
export type JobSettings = { recruiter_name: string; duration_minutes: number; availability: string; scheduling_link: string };
export type Reason = { id: string; source: string; title: string; detail: string; advice: string; action_type: string;
  selected: boolean };
export type Email = { id: string; application_id: string; job_id: string;
  kind: "not_moving_forward" | "scheduling" | "stage" | "nudge" | "answer" | "confirmation" | "offer";
  stage_id: string | null; batch_id: string | null; status: "draft" | "sent" | "failed" | "cancelled";
  intended_to: string | null; actual_to: string | null; subject: string; body: string; model: string; provider: string | null;
  reasons: { reasons: Reason[]; note: string | null } | null;
  checks: { errors?: string[]; model_errors?: string[]; warnings?: string[]; words?: number; evidence_id?: string | null;
    edited?: boolean };
  error: string | null; created_at: string; sent_at: string | null; candidate_name: string | null; template: boolean;
  label: string; attachments: { filename: string; content_type: string; size: number }[];
  in_reply_to?: string | null; token?: string | null };
export type Activity = { id: string; kind: "stage" | "email" | "decision" | "note"; summary: string; created_at: string;
  detail: Record<string, unknown>; email?: Email };
export type EmailConfig = { provider: string; demo_redirect: string | null; model: string | null };
export type BoardCard = Pick<ApplicantRow, "application_id" | "name" | "current_role" | "match" | "confidence" | "demo" |
  "status"> & { workflow: WorkflowBadge };
export type PipelineBoard = { stages: (Stage & { cards: BoardCard[] })[]; unstaged: number };

/** "Top 20%" for percentile 80: the share of candidates at or above, rounded to 5 (at least 5). */
export const topShare = (p: number) => Math.max(5, 5 * Math.round((100 - p) / 5));

// --- Hiring workflow (specs/16) ---------------------------------------------------------------------------
export type WorkflowBadge = { status: string; label: string; round?: string | null } | null;
export type Slot = { start: string; end: string; timezone: string; label: string; source: "availability" | "candidate";
  fits: boolean; note: string | null };
export type Reading = { label: "slot_chosen" | "times_proposed" | "question" | "decline" | "unclear"; quote: string;
  times: { quote: string; start: string; timezone: string }[]; question: string | null; method: string;
  model: string | null; notes: string[] };
export type Message = { id: string; direction: "out" | "in"; subject: string; body: string; token: string | null;
  message_id: string | null; actual_to: string | null; intended_to: string | null; from_addr: string | null;
  status: string; source: string; reading: Reading | null; sent_at: string | null; created_at: string };
export type Interview = { id: string; stage: string; start: string; end: string; timezone: string; status: string;
  label: string | null };
export type Scorecard = { id: string; interviewer: string; stage: string; recommendation: string; notes: string;
  ratings: { area: string; rating: number; notes: string }[]; created_at: string };
export type SummaryPoint = { text: string; quotes: { scorecard_id: string; quote: string }[] };
export type FeedbackSummary = { stage: string; scorecards: number; recommendations: Record<string, number>;
  agreements: SummaryPoint[]; disagreements: SummaryPoint[]; open_questions: SummaryPoint[]; method: string;
  model: string | null; notes: string[] };
export type WorkflowStep = { step: string; title: string; status: string; since: string | null; due: string | null;
  email_id?: string; purpose?: string; error?: string | null; email?: Email; reply_id?: string | null; reply?: Message;
  slots?: Slot[]; interview_id?: string | null; interview?: Interview | null; stage?: string | null; focus?: string[];
  scorecards?: Scorecard[]; summary?: FeedbackSummary | null; next?: string; options?: { key: string; label: string }[];
  unread: number };
export type TimelineItem = { key: string; name: string; group: string | null;
  status: "done" | "current" | "upcoming" | "skipped" | "not_reached" | "closed"; date: string | null; note?: string };
export type WorkflowView = {
  application: { id: string; name: string | null; job_id: string; job_title: string; company: string;
    board_stage: string | null; match: number | null; status: string; current_role: string | null };
  workflow: { status: string; status_label: string; step: string; stage: string; round: number; phase: string;
    outcome: string | null; next_due: string | null; token: string; created_at: string; badge: WorkflowBadge } | null;
  step: WorkflowStep | null; timeline: TimelineItem[]; rounds: { name: string; focus: string[] }[];
  thread: Message[]; drafts: Email[]; interviews: Interview[]; scorecards: Scorecard[];
  history: { type: string; actor: "recruiter" | "agent" | "candidate"; at: string; text: string }[];
  settings: JobSettings; canned_replies: { key: string; text: string }[];
};
export type RecruiterTask = { application_id: string; candidate: string; job_id: string; job_title: string;
  company: string; stage: string; step: string; title: string; since: string; kind: string };

// --- Create a job (specs/14) ------------------------------------------------------------------------------
export type JobStatus = "draft" | "open" | "closed";
export type JobRow = { id: string; title: string; company: string; location: string; workplace: string | null;
  applicants: number; created_at: string; updated_at: string | null; status: JobStatus; has_draft: boolean;
  step: number | null; baseline_id: string | null };
export type ReqRow = { key: string; criterion_id: string | null; name: string; kind: "skill" | "tool" | "certification";
  priority: "required" | "preferred"; must_have: boolean; aliases: string[]; suggested_aliases: string[];
  description: string; from_jd: boolean; quote: string | null };
export type JobDetails = { title: string; company: string; department: string; location: string;
  workplace: "on_site" | "hybrid" | "remote" | null; employment_type: string; level: string; min_years: number | null;
  short_description: string; responsibilities: string[]; role_family?: string; role_family_auto?: boolean };
export type JobDraft = { step: number; source: "jd" | "copy" | "scratch" | "edit"; details: JobDetails;
  detail_quotes: Record<string, string>; requirements: ReqRow[];
  good: { scope_expectation: string; complexity_criteria: string; expected_reach: number | null };
  good_quotes: Record<string, string | null>; ranking: { preset: string | null; weights: Weights };
  plan: { stages: string[] } & JobSettings;
  extraction: { status: string; model: string | null; notice: string | null;
    dropped: { item: string; text: string; reason: string; quote?: string }[] | null } | null };
export type JobForm = { job_id: string; status: JobStatus; draft: JobDraft; jd_text: string | null; has_jd_file: boolean;
  warnings: string[]; applicants: number };
export type Posting = { id: string; title: string; company: string; department: string; location: string;
  workplace: string | null; employment_type: string; level: string; min_years: number | null;
  short_description: string; responsibilities: string[]; required: string[]; preferred: string[];
  reach: string | null; created_at: string; posted_at?: string; status?: JobStatus; jd_text?: string | null; has_jd_file?: boolean };
export type JobTemplate = { kind: "job" | "template"; id: string; title: string; company: string; status: JobStatus | null;
  level?: string };
export const WORKPLACE: Record<string, string> = { on_site: "On-site", hybrid: "Hybrid", remote: "Remote" };
export const REACH: Record<number, string> = { 1: "Own work", 2: "Team", 3: "Cross-functional", 4: "Organization", 5: "External" };

// --- Market baselines (specs/15) ---------------------------------------------------------------------------
export type MarketCriterion = { name: string; kind: string; share: number; required_share: number;
  by_tier: Record<string, number>; postings: number; companies: number; category: "required" | "preferred" | "dropped";
  previous: string | null; change: "new" | "dropped" | "moved" | null; share_delta: number | null; variants: string[];
  quotes: string[] };
export type MarketVersion = { id: string; role: string; level: string; version: number; window_start: string;
  window_end: string; postings: number; companies: number; status: "draft" | "published" | "discarded" | "retired";
  basis: string; left_out_note?: string | null; baseline_id: string | null; summary_method: string; created_at: string;
  published_at: string | null;
  content: { criteria: MarketCriterion[]; required_years: number | null; expected_reach: number | null;
    scope_expectation: string | null; complexity_criteria: string | null; description: string | null;
    tiers: Record<string, { companies: number; postings: number }>; material: string[];
    thresholds: Record<string, unknown> } };
export type MarketRole = { key: string; title: string; manual_baseline: string; source: "manual" | "market";
  active: MarketVersion | null; latest: MarketVersion | null; postings: number; companies: number;
  configured: { companies: number; placeholders: number; fetchable: number }; levels: Record<string, number> };
export type MarketLabel = { version: number; date: string; baseline_id: string; basis: string; label: string };
export type RunBaselines = { role: string | null; role_title?: string; current: "standard" | "market";
  current_version?: number | null; standard: { baseline_id: string; label: string } | null; market: MarketLabel | null };
export type MarketView = MarketLabel & { role: string; title: string; postings: number; companies: number;
  window: [string, string]; required_years: number | null; expected_reach: string | null;
  criteria: { name: string; kind: string; category: "required" | "preferred"; company_share: number; share: number;
    postings: number; companies: number }[] };
export type MarketPosting = { id: string; company: string; tier: string; source: string; title: string; level: string | null;
  level_method: string | null; source_url: string | null; first_seen: string; last_seen: string; extracted: boolean };
export type SideBySide = { manual: { required_years: number; expected_reach: string; scope_expectation: string | null;
    complexity_criteria: string | null };
  market: { version: number; required_years: number | null; expected_reach: string | null; scope_expectation: string | null;
    complexity_criteria: string | null } | null;
  rows: { name: string; kind: string; manual: string | null; market: string | null; share: number | null }[] };
export type Suggestion = { id: string; name: string; canonical: string | null; method: string | null; status: string };

// --- Candidate experience (specs/17) -------------------------------------------------------------------------
export type OnboardingStep = "proof" | "roles" | "review" | "scores" | "details" | "done";
export type Target = { baseline_id: string; title: string; is_primary: boolean };
export type OnboardingState = { candidate: Candidate;
  onboarding: { step: OnboardingStep; read_task_id: string | null; score_task_id: string | null;
    confirmed_at: string | null; finished_at: string | null };
  targets: Target[]; read_task: Task | null; score_task: Task | null; awaiting_review: boolean;
  details: { saved_at: string | null; dismissed_at: string | null }; roles: { baseline_id: string; title: string }[] };
export type CardAction = { key: string; title: string; type: string; type_label: string; points: number; points_text: string;
  covers_criteria: string[]; done: boolean };
export type ScoreCard = Target & { run_id: string | null; status: "scored" | "running" | "not_scored";
  final: number | null; dimensions: Record<string, number | null>;
  percentile: { shown: boolean; top: number | null; value: number | null; pool_size: number; min_pool: number } | null;
  plan_status: "ready" | "generating" | "failed" | "deferred" | "none" | null; actions: CardAction[];
  gaps: { label: string; dimension: string; gain_text: string; status: string }[]; thin: boolean;
  next_action: CardAction | null; done_actions: CardAction[];
  baseline_source: "standard" | "market" };
export type YesNo = "yes" | "no";
export type ApplicationDetails = {
  work_authorization: { country: string; authorized: YesNo | null; sponsorship_now: YesNo | "decline";
    sponsorship_future: YesNo | "decline" };
  questions: { earliest_start: string | null; notice_period: string | null; salary_min: number | null;
    salary_currency: string; relocate: YesNo | null; work_mode: string | null; locations: string[]; links: string[] };
  eeo: { gender: string; race_ethnicity: string; veteran_status: string; disability_status: string };
  saved_at: string | null; dismissed_at: string | null };
export const STEP_LABELS: { key: Exclude<OnboardingStep, "done">; label: string }[] = [
  { key: "proof", label: "Add your proof" }, { key: "roles", label: "Target roles" }, { key: "review", label: "Review" },
  { key: "scores", label: "Your baseline scores" }, { key: "details", label: "Finish your profile" }];
export type JobActivity = { last_at: string | null; days: number | null; label: string; low: boolean; reviewing: boolean };
export type JobMatch = { value: number; must_haves_met: number; must_haves_total: number; preview: true } | null;
export type Salary = { min: number | null; max: number | null; currency: string } | null;
export type HomeJob = { id: string; title: string; company: string; location: string; workplace: string | null;
  salary: Salary; posted_at: string; activity: JobActivity; jobmatch: JobMatch; group: "strong" | "close" | "stretch" | null;
  role_baseline_id: string | null; target: boolean; saved: boolean; hidden: boolean;
  applied: { status: string; applied_at: string } | null; demo: boolean };
export type HomeJobs = { jobs: HomeJob[]; hidden: number; hidden_jobs: { id: string; title: string; company: string;
    reason: string | null }[]; total_open: number; preview: boolean; profile_confirmed: boolean;
  thresholds: { strong: number; close: number }; targets: Target[]; defaults: { workplace: string | null } };
export type JobRequirement = { name: string; kind: string; priority: "required" | "preferred"; must_have: boolean;
  evidence: { text: string; quote: string | null; source: string | null; matched: string; caption: string } | null };
export type JobDetail = { job: Posting & { salary: Salary; status: string; demo: boolean }; activity: JobActivity;
  jobmatch: JobMatch; role: { baseline_id: string | null; title: string | null; baseline_score: number | null; run_id: string | null };
  requirements: JobRequirement[]; evidence_matches: number;
  raise: { title: string; what_to_do: string; points: number; covers: string[] }[]; raise_fallback: boolean;
  plan_status: string | null; applied: { status: string; applied_at: string } | null; profile_confirmed: boolean;
  saved: boolean; recruiter_view: { name: string | null; sources: string[]; roles: string[]; projects: string[];
    top_evidence: { name: string; quote: string | null; source: string | null }[] } };
export const HIDE_REASONS: [string, string][] = [["location", "Location"], ["salary", "Salary"], ["role", "Not my kind of role"],
  ["seniority", "Seniority"], ["other", "Other"]];

export function salaryText(s: Salary): string | null {
  if (!s) return null;
  const k = (n: number | null) => (n === null ? "" : n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`);
  const sym = s.currency === "USD" ? "$" : `${s.currency} `;
  return s.min && s.max ? `${sym}${k(s.min)}–${k(s.max)}` : `${sym}${k(s.min ?? s.max)}`;
}

export function postedAgo(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "Posted today" : days === 1 ? "Posted yesterday" : `Posted ${days} days ago`;
}
export type WaitTip = { title: string; text: string; icon: string | null };
export type ReadingSource = { type: "resume" | "linkedin_pdf" | "github" | "portfolio"; label: string;
  status: "waiting" | "reading" | "done" | "failed"; error: string | null;
  counts: { roles: number; projects: number; skills: number } | null };
export type ReadingProgress = { sources: ReadingSource[]; stage: string; stage_label: string; percent: number; done: boolean;
  error: string | null; typical_minutes: number | null; review_ready?: boolean };
export type ScoringRole = { baseline_id: string; title: string; is_primary: boolean;
  status: "waiting" | "scoring" | "ready" | "failed"; dimensions: Record<string, number> };
export type ScoringProgress = { roles: ScoringRole[]; overall: number; line: string; all_ready: boolean; running: boolean;
  typical_minutes: number | null };
export type MyApplication = { application_id: string; job_id: string; title: string; company: string; applied_at: string;
  status: "Applied" | "Viewed by recruiter" | "Shortlisted" | "Interview" | "Offer" | "Hired" | "Declined" | "Not moving forward";
  round: string | null; final: boolean;
  timeline: { key: string; label: string; state: "done" | "current" | "upcoming" | "not_reached"; date: string | null }[];
  reasons: { title: string; detail: string; advice: string; link: string | null }[]; waiting: boolean;
  waiting_text: string | null };

// --- Uploads (specs/19 item 2) ------------------------------------------------------------------------------------
/** The upload size limit from the server's config (10 MB until it answers). */
export function useUploadLimit(): number {
  const [max, setMax] = useState(10 * 1024 * 1024);
  useEffect(() => { get<{ max_bytes: number }>("/settings/uploads").then((s) => setMax(s.max_bytes)).catch(() => {}); }, []);
  return max;
}

// --- Weight assistant (specs/21) ------------------------------------------------------------------------------------
export type WeightChange = { key: string; label: string; dimension: string | null; current: number; suggested: number;
  reason?: string };
export type PreviewRow = { application_id: string; name: string; match: number; match_before: number; rank: number;
  was: number; move: number };
export type WeightPreview = { available: true; top: PreviewRow[]; up: number; down: number; same: number; total: number }
  | { available: false; message: string };
export type WeightSuggestion = { id: string; text: string; created_at: string; summary: string; label: string;
  weights: Weights; changes: WeightChange[]; unchanged: WeightChange[]; ignored: string[]; ignored_notice: string | null;
  applied: boolean; applied_at: string | null; undone_at: string | null; preview: WeightPreview };
export type SuggestResult = { status: "ok"; suggestion: WeightSuggestion } | { status: "unavailable" | "invalid"; message: string };
export type SuggestionSummary = { id: string; created_at: string; text: string; applied: boolean; undone: boolean };
