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
export type Role = { id: string; title: string; employer: string | null; start_date: string | null;
  end_date: string | null; is_current: boolean; bullets: Bullet[]; review_reasons: string[]; quote: string | null;
  source: string; industry: string | null; team_size: number | null };
export type Project = { id: string; kind: string; name: string; description: string | null; url: string | null;
  start_date: string | null; end_date: string | null; bullets: Bullet[]; review_reasons: string[];
  quote: string | null; is_fork: boolean | null; own_commits: number | null; source: string };
export type Claim = { id: string; kind: string; name_raw: string; bullet_id: string | null; source: string;
  quote: string | null };
export type Certification = { id: string; name: string; issuer: string | null; credential_id: string | null;
  verify_url: string | null; source: string };
export type Dropped = { id: string; item_type: string; text: string; reason: string };
export type MediaLink = { id: string; url: string; kind: "youtube" | "loom"; title: string | null; embed_url: string };
export type ProfileResponse = {
  candidate: Candidate;
  profile: { roles: Role[]; projects: Project[]; claims: Claim[]; certifications: Certification[] };
  dropped: Dropped[]; warnings: string[]; blocker: string | null; media_links: MediaLink[];
  sources: { id: string; type: string; status: string; text: string | null }[];
};

export type Judgment = { question: string; subject_type: string; subject: string; label: string | null;
  method: string | null; model_version: string | null; quote: string | null; confidence: number | null;
  reasoning: string | null };
export type Subcategory = { key: string; label: string; dimension: string; status: "scored" | "missing" | "na" | "error";
  score: number | null; guidance: string | null; formula: string | null; weight: number; effective_weight: number;
  details: Record<string, unknown> & { judgments?: Judgment[] } };
export type Gap = { subcategory: string; label: string; dimension: string; status: string; score: number;
  gain: number; gain_text: string; guidance: string | null };
export type Weights = { dimensions: Record<string, number>; subcategories: Record<string, Record<string, number>> };
export type RunView = { run: { id: string; candidate_id: string; baseline_id: string; plan_status: string | null };
  weights: Weights; final: number | null; dimensions: Record<string, number | null>; subcategories: Subcategory[];
  gaps: Gap[] };
export type PlanAction = { type: string; type_label: string; title: string; what_to_do: string; must_show: string[]; addresses: string[];
  covers_criteria: string[]; builds_on: string | null; points: number; points_text: string };
export type Plan = { status: "ready" | "generating" | "failed" | "none"; error?: string | null; actions: PlanAction[];
  quick_wins?: string[]; not_actionable?: string[]; gaps: Gap[] };

export const DIMENSIONS = ["experience", "expertise", "authority", "trust"] as const;
export const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
