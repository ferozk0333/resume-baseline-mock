"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ChevronDown, Copy, ExternalLink, GitFork, Info, Mail, Workflow } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/app/score/subcategory-row";
import { API, DIMENSIONS, get, titleCase, topShare, type Activity, type Panel, type Stage } from "@/lib/api";
import { cn } from "@/lib/utils";

export const TABS = ["overview", "requirements", "eeat", "resume", "work", "linkedin", "activity"] as const;
export type Tab = (typeof TABS)[number];

const MUST: Record<string, { label: string; className: string }> = {
  met_backed: { label: "met, backed", className: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  met_on_paper: { label: "on paper", className: "bg-amber-50 text-amber-800 border-amber-200" },
  not_met: { label: "not met", className: "bg-red-50 text-red-700 border-red-200" },
  unknown: { label: "not scored", className: "bg-slate-100 text-slate-600 border-slate-200" },
};
const BACKING: Record<string, string> = { artifact: "text-emerald-700", "in a bullet": "text-sky-700",
  certification: "text-emerald-700", "listed only": "text-amber-700", "not shown": "text-muted-foreground",
  "not judged": "text-muted-foreground", "not scored yet": "text-muted-foreground" };

export function ProfilePanel({ p, tab, onTab, stages, onStatus, onStage, onReject, onStageEmail, activityKey }: {
  p: Panel; tab: Tab; onTab: (t: Tab) => void; stages: Stage[];
  onStatus: (s: "new" | "shortlisted") => void; onStage: (stageId: string) => void;
  onReject: () => void; onStageEmail: () => void; activityKey: number;
}) {
  const backed = p.must_haves.filter((m) => m.status === "met_backed").length;
  const workCount = p.work.videos.length + p.work.repos.length + p.work.portfolio.length;
  const has: Record<Tab, boolean> = {
    overview: true, requirements: p.requirements.length > 0, eeat: !!p.dimensions, resume: p.work.resume_file,
    work: workCount > 0 || !!p.work.portfolio_url, linkedin: !!p.work.linkedin || !!p.work.linkedin_link, activity: true,
  };
  const label: Record<Tab, string> = { overview: "Overview", requirements: "Requirements", eeat: "EEAT",
    resume: "Resume", work: `Work (${workCount})`, linkedin: "LinkedIn", activity: "Activity" };
  const current = has[tab] ? tab : "overview";
  const inLoop = p.status === "shortlisted" || p.status === "in_loop";

  return (
    <article aria-label={`Profile of ${p.name ?? "applicant"}`} className="flex h-full min-h-0 flex-col">
      {/* Sticky header */}
      <header className="shrink-0 space-y-2 border-b px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-xl font-semibold">{p.name ?? "Unnamed"}</h2>
              {p.demo && <Badge variant="outline" className="text-muted-foreground">{p.demo}</Badge>}
            </div>
            <p className="text-sm text-muted-foreground">{p.current_role ?? "No current role"}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/recruiter/applications/${p.application_id}/workflow`} className={buttonVariants({ size: "sm", variant: "outline" })}>
              <Workflow /> Workflow
            </Link>
            {inLoop && (
              <>
                <select aria-label="Stage" value={p.stage_id ?? ""} onChange={(e) => onStage(e.target.value)}
                  className="h-7 rounded-lg border border-input bg-white px-2 text-sm">
                  {stages.filter((s) => !s.terminal).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <Button size="sm" variant="outline" onClick={onStageEmail}><Mail /> Stage email</Button>
              </>
            )}
            {!inLoop && p.status !== "not_moving_forward" && (
              <Button size="sm" variant="outline" onClick={() => onStatus("shortlisted")}>Shortlist</Button>
            )}
            {inLoop && p.status === "shortlisted" && (
              <Button size="sm" variant="secondary" onClick={() => onStatus("new")}><Check /> Shortlisted</Button>
            )}
            {p.status === "not_moving_forward" ? (
              <Button size="sm" variant="secondary" onClick={() => onStatus("new")}>Not moving forward · undo</Button>
            ) : (
              <Button size="sm" variant="outline" onClick={onReject}>Not moving forward</Button>
            )}
          </div>
        </div>
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <Stat label="Match" value={p.match === null ? "—" : p.match.toFixed(1)} accent />
          <Stat label="Evidence confidence" value={p.confidence === null ? "—" : String(Math.round(p.confidence))}
            hint="The Trust score (0–100): how well the profile's claims are backed by artifacts, references and sources that agree." />
          <Stat label="Must-haves" value={p.scored ? `${backed} of ${p.must_haves.length}` : "—"} hint="Met and backed by work." />
          {p.percentile !== null && p.percentile !== undefined && (
            <Stat label="Percentile" value={`Top ${topShare(p.percentile)}%`}
              hint={`Higher than ${p.percentile}% of ${p.percentile_n ?? "the"} other candidates for this role (${p.job.title}).`} />
          )}
          {p.stage && <Stat label="Stage" value={p.stage} />}
        </dl>
      </header>

      <Tabs value={current} onValueChange={(v) => onTab(v as Tab)} className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList variant="line" className="h-9 w-full shrink-0 justify-start rounded-none border-b px-4">
          {TABS.map((t) => (
            <span key={t} title={has[t] ? undefined : "Not provided"} className="inline-flex">
              <TabsTrigger value={t} disabled={!has[t]} className="flex-none px-2.5">{label[t]}</TabsTrigger>
            </span>
          ))}
        </TabsList>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <TabsContent value="overview"><Overview p={p} /></TabsContent>
          <TabsContent value="requirements"><Requirements p={p} /></TabsContent>
          <TabsContent value="eeat"><Eeat p={p} /></TabsContent>
          <TabsContent value="resume"><Resume p={p} /></TabsContent>
          <TabsContent value="work"><Work p={p} /></TabsContent>
          <TabsContent value="linkedin"><LinkedIn p={p} /></TabsContent>
          <TabsContent value="activity">
            <p className="mb-3 text-sm"><Link href={`/recruiter/applications/${p.application_id}/workflow`} className="text-primary hover:underline">
              Open the hiring workflow</Link> <span className="text-muted-foreground">for the email thread, replies, interviews and feedback.</span></p>
            <ActivityTab id={p.application_id} refresh={activityKey} />
          </TabsContent>
        </div>
      </Tabs>
    </article>
  );
}

// --- Overview: fits one screen at 1440×900 (one line per item, expand on click) ----------------------------

function Line({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
      className={cn("w-full text-left hover:text-foreground", open ? "" : "line-clamp-1", className)}>{text}</button>
  );
}

function Overview({ p }: { p: Panel }) {
  if (!p.scored) {
    return <p className="rounded-lg border border-dashed bg-white px-4 py-3 text-sm text-muted-foreground">
      Not scored yet: see Work, Resume and LinkedIn. Scores, “why” and questions appear after judging.</p>;
  }
  return (
    <div className="grid gap-x-8 gap-y-5 xl:grid-cols-2">
      <Section title="Why this candidate" badge={p.generated_label}>
        <ul className="space-y-1.5 text-sm">
          {p.why_points.map((w, i) => <li key={i} className="flex gap-2"><span className="text-primary">•</span><Line text={w.text} /></li>)}
        </ul>
      </Section>
      <Section title="Key differentiators">
        <div className="mb-2 flex flex-wrap gap-1">
          {p.differentiators.tags.map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}
        </div>
        <ul className="space-y-1.5 text-sm">
          {p.differentiators.evidence.map((e, i) => (
            <li key={i} className="min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="shrink-0 font-medium">{e.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {e.link ? <a href={e.link} target="_blank" rel="noreferrer" className="text-primary hover:underline">{e.source}</a> : e.source}
                </span>
              </div>
              <Line text={`“${e.quote}”`} className="text-xs text-muted-foreground" />
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Must-haves">
        <div className="flex flex-wrap gap-1.5">
          {p.must_haves.map((m) => (
            <Badge key={m.criterion_id} variant="outline" className={MUST[m.status].className}>{m.name} · {MUST[m.status].label}</Badge>
          ))}
        </div>
      </Section>
      {p.questions.length > 0 && (
        <Section title="Ask on the phone screen" badge={p.generated_label} action={
          <Button size="xs" variant="ghost" onClick={async () => {
            await navigator.clipboard.writeText(p.questions.map((q, i) => `${i + 1}. ${q.question}`).join("\n"));
            toast.success("Questions copied");
          }}><Copy /> Copy</Button>}>
          <ol className="space-y-1 text-sm">
            {p.questions.map((q, i) => <li key={i} className="flex gap-1.5"><span>{i + 1}.</span><Line text={q.question} /></li>)}
          </ol>
        </Section>
      )}
    </div>
  );
}

function Requirements({ p }: { p: Panel }) {
  return (
    <Table>
      <TableHeader><TableRow>
        <TableHead>Requirement</TableHead><TableHead>What they showed</TableHead><TableHead>Backing</TableHead><TableHead>Source</TableHead>
      </TableRow></TableHeader>
      <TableBody>
        {p.requirements.map((r) => (
          <TableRow key={r.criterion_id}>
            <TableCell className="align-top">
              <div className="font-medium">{r.name}</div>
              <div className="flex gap-1 pt-1">
                {r.must_have && <Badge className="text-[10px]">must-have</Badge>}
                <Badge variant="outline" className="text-[10px]">{r.priority}</Badge>
              </div>
            </TableCell>
            <TableCell className="max-w-md align-top text-xs whitespace-normal">{r.quote ? `“${r.quote}”` : "—"}</TableCell>
            <TableCell className={cn("align-top text-xs", BACKING[r.backing])}>{r.backing}</TableCell>
            <TableCell className="align-top text-xs whitespace-normal">
              {r.link ? <a href={r.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                {r.source} <ExternalLink className="size-3" /></a> : r.source ?? "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Eeat({ p }: { p: Panel }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {DIMENSIONS.map((d) => (
        <div key={d} className="rounded-lg border bg-white">
          <div className="flex items-center justify-between border-b px-3 py-2 text-sm font-medium">
            {titleCase(d)}
            <span className="tabular-nums">{p.dimensions![d] === null ? "—" : Math.round((p.dimensions![d] ?? 0) * 100)}</span>
          </div>
          <ul className="divide-y text-xs">
            {p.subcategories.filter((s) => s.dimension === d).map((s) => (
              <li key={s.key}>
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5">
                    <ChevronDown className="size-3 -rotate-90 transition-transform group-open:rotate-0" />
                    <span className="flex-1">{s.label}</span>
                    <StatusBadge status={s.status} />
                    <span className="w-9 text-right tabular-nums">{s.score === null ? "—" : s.score.toFixed(2)}</span>
                  </summary>
                  <div className="space-y-1 px-8 pb-2 text-muted-foreground">
                    {s.evidence.length === 0 && <p>{s.guidance ?? (s.status === "scored"
                      ? "Computed in code from the roles' and projects' relevance and dates (see Relevance)."
                      : "No evidence judged.")}</p>}
                    {s.evidence.map((e, i) => (
                      <p key={i}><span className={cn("font-medium", e.supported ? "text-emerald-700" : "text-muted-foreground")}>
                        {e.label}</span>{e.text ? ` — ${e.text}` : ""}</p>
                    ))}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Resume({ p }: { p: Panel }) {
  return p.work.resume_file_kind === "pdf" ? (
    <iframe src={`${API}/candidates/${p.candidate_id}/files/resume`} title="Resume" className="h-[calc(100vh-17rem)] w-full rounded-lg border bg-white" />
  ) : (
    <a href={`${API}/candidates/${p.candidate_id}/files/resume`} className="text-sm text-primary hover:underline">
      Download the resume ({p.work.resume_file_kind})</a>
  );
}

function Work({ p }: { p: Panel }) {
  return (
    <div className="space-y-5">
      {p.work.videos.length > 0 && (
        <div>
          <h4 className="text-sm font-medium">Project demo videos</h4>
          <p className="mb-2 text-xs text-muted-foreground">Walkthroughs of the demo, architecture and design choices, from the candidate.</p>
          <div className="grid gap-3 lg:grid-cols-2">
            {p.work.videos.map((v) => (
              <iframe key={v.id} src={v.embed_url} title={v.title ?? `${v.kind} demo video`} loading="lazy"
                allow="encrypted-media; picture-in-picture" allowFullScreen className="aspect-video w-full rounded-lg border" />
            ))}
          </div>
        </div>
      )}
      {p.work.repos.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium">GitHub</h4>
          <div className="grid gap-3 lg:grid-cols-2">
            {p.work.repos.map((r) => (
              <div key={r.id} className="rounded-lg border bg-white p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  {r.url ? <a href={r.url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">{r.name}</a>
                    : <span className="font-medium">{r.name}</span>}
                  {r.fork && <Badge variant="outline" className="gap-1"><GitFork className="size-3" />fork{r.counted ? "" : " · not counted"}</Badge>}
                </div>
                {r.description && <p className="mt-1 text-muted-foreground">{r.description}</p>}
                <p className="mt-1 text-xs text-muted-foreground">
                  {[r.languages, r.own_commits !== null && `${r.own_commits} own commits`, r.last_update && `updated ${r.last_update}`]
                    .filter(Boolean).join(" · ")}
                </p>
                {r.readme_excerpt && <p className="mt-2 line-clamp-3 text-xs">{r.readme_excerpt}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
      {(p.work.portfolio.length > 0 || p.work.portfolio_url) && (
        <div>
          <h4 className="mb-2 text-sm font-medium">Portfolio</h4>
          <div className="grid gap-3 lg:grid-cols-2">
            {p.work.portfolio.map((x, i) => (
              <div key={i} className="rounded-lg border bg-white p-3 text-sm">
                <div className="font-medium">{x.name}</div>
                {x.description && <p className="mt-1 text-muted-foreground">{x.description}</p>}
              </div>
            ))}
          </div>
          {p.work.portfolio_url && <a href={p.work.portfolio_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-primary hover:underline">Open the portfolio</a>}
        </div>
      )}
    </div>
  );
}

function LinkedIn({ p }: { p: Panel }) {
  const li = p.work.linkedin;
  const link = p.work.linkedin_link;
  return (
    <div className="space-y-3 text-sm">
      {link && (
        <p className="flex flex-wrap items-center gap-2">
          <a href={link.url} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border bg-white px-2.5 py-1 font-medium text-primary hover:bg-muted">
            <ExternalLink className="size-3.5" /> {link.url.replace("https://www.", "")}
          </a>
          <span className="text-xs text-muted-foreground">Profile link {link.source}</span>
        </p>
      )}
      {!li && <p className="text-xs text-muted-foreground">No LinkedIn PDF uploaded.</p>}
      {li && <LinkedInPdf li={li} />}
    </div>
  );
}

function LinkedInPdf({ li }: { li: NonNullable<Panel["work"]["linkedin"]> }) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">From the uploaded LinkedIn PDF</p>
      <ul className="space-y-2">
        {li.roles.map((r) => (
          <li key={r.id}>
            <div className="font-medium">{r.title}{r.employer ? ` — ${r.employer}` : ""}</div>
            <div className="text-xs text-muted-foreground">{[r.start_date, r.is_current ? "present" : r.end_date].filter(Boolean).join(" to ")}</div>
            {r.bullets.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs">{r.bullets.map((b) => <li key={b.id}>{b.text}</li>)}</ul>}
          </li>
        ))}
      </ul>
      {li.claims.length > 0 && <p className="text-xs text-muted-foreground">Skills: {li.claims.map((c) => c.name_raw).join(", ")}</p>}
      {li.certifications.length > 0 && <p className="text-xs text-muted-foreground">Certifications: {li.certifications.map((c) => c.name).join(", ")}</p>}
    </div>
  );
}

function ActivityTab({ id, refresh }: { id: string; refresh: number }) {
  const [items, setItems] = useState<Activity[] | null>(null);
  useEffect(() => { get<Activity[]>(`/applications/${id}/activity`).then(setItems).catch(() => setItems([])); }, [id, refresh]);
  if (items === null) return null;
  if (!items.length) return <p className="text-sm text-muted-foreground">No activity yet: stage moves, emails and decisions appear here.</p>;
  return (
    <ol className="space-y-3 text-sm">
      {items.map((a) => (
        <li key={a.id} className="border-l-2 pl-3">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-[10px]">{a.kind}</Badge>
            <span className="font-medium">{a.summary}</span>
            <span className="ml-auto text-xs text-muted-foreground">{new Date(a.created_at).toLocaleString()}</span>
          </div>
          {a.email && (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-primary">Show the email ({a.email.label})</summary>
              <div className="mt-1 rounded-md border bg-white p-3 text-xs">
                <div className="text-muted-foreground">To {a.email.actual_to}{a.email.intended_to && a.email.intended_to !== a.email.actual_to
                  ? ` (intended: ${a.email.intended_to})` : ""}</div>
                <div className="font-medium">{a.email.subject}</div>
                <pre className="mt-2 font-[inherit] whitespace-pre-wrap">{a.email.body}</pre>
              </div>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

function Stat({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: boolean }) {
  return (
    <div>
      <dt className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}{hint && <span title={hint} aria-label={hint}><Info className="size-3" /></span>}
      </dt>
      <dd className={cn("text-lg leading-6 font-semibold tabular-nums", accent && "text-primary")}>{value}</dd>
    </div>
  );
}

function Section({ title, badge, action, children }: { title: string; badge?: string | null; action?: React.ReactNode;
  children: React.ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {badge && <Badge variant="outline" className="border-amber-300 bg-amber-50 text-[10px] text-amber-800">{badge}</Badge>}
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}
