"use client";
import { Check, Copy, ExternalLink, GitFork, Info } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge } from "@/components/app/score/subcategory-row";
import { API, DIMENSIONS, titleCase, type Panel } from "@/lib/api";
import { cn } from "@/lib/utils";

const MUST: Record<string, { label: string; className: string }> = {
  met_backed: { label: "met, backed", className: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  met_on_paper: { label: "on paper", className: "bg-amber-50 text-amber-800 border-amber-200" },
  not_met: { label: "not met", className: "bg-red-50 text-red-700 border-red-200" },
  unknown: { label: "not scored", className: "bg-slate-100 text-slate-600 border-slate-200" },
};
const BACKING: Record<string, string> = { artifact: "text-emerald-700", "in a bullet": "text-sky-700",
  "claimed only": "text-amber-700", "not shown": "text-muted-foreground" };

export function ProfilePanel({ p, onStatus }: { p: Panel; onStatus: (s: Panel["status"]) => void }) {
  const backed = p.must_haves.filter((m) => m.status === "met_backed").length;
  return (
    <article aria-label={`Profile of ${p.name ?? "applicant"}`} className="space-y-6">
      {/* 1. Header */}
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold">{p.name ?? "Unnamed"}</h2>
            <p className="text-sm text-muted-foreground">{p.current_role ?? "No current role"}</p>
            {p.demo && <Badge variant="outline" className="mt-1 text-muted-foreground">{p.demo}</Badge>}
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant={p.status === "shortlisted" ? "default" : "outline"}
              onClick={() => onStatus(p.status === "shortlisted" ? "new" : "shortlisted")}>
              {p.status === "shortlisted" ? <><Check /> Shortlisted</> : "Shortlist"}
            </Button>
            <Button size="sm" variant={p.status === "not_moving_forward" ? "secondary" : "outline"}
              onClick={() => onStatus(p.status === "not_moving_forward" ? "new" : "not_moving_forward")}>
              Not moving forward
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Match score" value={p.match === null ? "—" : p.match.toFixed(1)} accent />
          <Stat label="Evidence confidence" value={p.confidence === null ? "—" : String(Math.round(p.confidence))}
            hint="The Trust score (0–100): how well the profile's claims are backed by artifacts, references and sources that agree." />
          <Stat label="Must-haves" value={p.scored ? `${backed} of ${p.must_haves.length}` : "—"} hint="Met and backed by work." />
        </div>
        {p.must_haves.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {p.must_haves.map((m) => (
              <Badge key={m.criterion_id} variant="outline" className={MUST[m.status].className}>
                {m.name} · {MUST[m.status].label}
              </Badge>
            ))}
          </div>
        )}
      </header>

      {!p.scored && (
        <p className="rounded-lg border border-dashed bg-white px-4 py-3 text-sm text-muted-foreground">
          Not scored yet: the profile is shown below; scores, “why” and questions appear after judging.
        </p>
      )}

      {/* 2. Why */}
      {p.why_points.length > 0 && (
        <Section title="Why this candidate" badge={p.generated_label}>
          <ul className="space-y-2 text-sm">
            {p.why_points.map((w, i) => <li key={i} className="flex gap-2"><span className="text-primary">•</span>{w.text}</li>)}
          </ul>
        </Section>
      )}

      {/* 3. Requirements vs evidence */}
      <Section title="Requirements vs. evidence">
        <div className="overflow-x-auto">
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
                  <TableCell className="max-w-72 align-top text-xs whitespace-normal">{r.quote ? `“${r.quote}”` : "—"}</TableCell>
                  <TableCell className={cn("align-top text-xs", BACKING[r.backing])}>{r.backing}</TableCell>
                  <TableCell className="align-top text-xs whitespace-normal">
                    {r.link ? <a href={r.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                      {r.source} <ExternalLink className="size-3" /></a> : r.source ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      {/* 4. Questions */}
      {p.questions.length > 0 && (
        <Section title="Ask on the phone screen" badge={p.generated_label} action={
          <Button size="sm" variant="outline" onClick={async () => {
            await navigator.clipboard.writeText(p.questions.map((q, i) => `${i + 1}. ${q.question}`).join("\n"));
            toast.success("Questions copied");
          }}><Copy /> Copy questions</Button>}>
          <ol className="space-y-3 text-sm">
            {p.questions.map((q, i) => (
              <li key={i}>
                <div>{i + 1}. {q.question}</div>
                <div className="text-xs text-muted-foreground">{q.topic} — {q.reason}</div>
              </li>
            ))}
          </ol>
        </Section>
      )}

      {/* 5. EEAT */}
      {p.dimensions && (
        <Section title="EEAT breakdown">
          <div className="grid gap-4 md:grid-cols-2">
            {DIMENSIONS.map((d) => (
              <div key={d} className="rounded-lg border bg-white">
                <div className="flex items-center justify-between border-b px-3 py-2 text-sm font-medium">
                  {titleCase(d)}
                  <span className="tabular-nums">{p.dimensions![d] === null ? "—" : Math.round((p.dimensions![d] ?? 0) * 100)}</span>
                </div>
                <ul className="divide-y text-xs">
                  {p.subcategories.filter((s) => s.dimension === d).map((s) => (
                    <li key={s.key} className="flex items-center gap-2 px-3 py-1.5">
                      <span className="flex-1">{s.label}</span>
                      <StatusBadge status={s.status} />
                      <span className="w-9 text-right tabular-nums">{s.score === null ? "—" : s.score.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* 6. Their work */}
      <Section title="Their work, in one place">
        <div className="space-y-5">
          {p.work.videos.length > 0 && (
            <div className="grid gap-3 md:grid-cols-2">
              {p.work.videos.map((v) => (
                <iframe key={v.id} src={v.embed_url} title={v.title ?? `${v.kind} demo video`} loading="lazy"
                  allow="encrypted-media; picture-in-picture" allowFullScreen className="aspect-video w-full rounded-lg border" />
              ))}
            </div>
          )}
          {p.work.repos.length > 0 && (
            <div>
              <h4 className="mb-2 text-sm font-medium">GitHub</h4>
              <div className="grid gap-3 md:grid-cols-2">
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
              <ul className="space-y-1 text-sm">
                {p.work.portfolio.map((x, i) => <li key={i}><span className="font-medium">{x.name}</span>{x.description && <span className="text-muted-foreground"> — {x.description}</span>}</li>)}
              </ul>
              {p.work.portfolio_url && <a href={p.work.portfolio_url} target="_blank" rel="noreferrer" className="text-sm text-primary hover:underline">Open the portfolio</a>}
            </div>
          )}
          {p.work.linkedin && (
            <div>
              <h4 className="text-sm font-medium">LinkedIn</h4>
              <p className="mb-2 text-xs text-muted-foreground">From the uploaded LinkedIn PDF</p>
              <ul className="space-y-1 text-sm">
                {p.work.linkedin.roles.map((r) => <li key={r.id}>{r.title}{r.employer ? ` — ${r.employer}` : ""}</li>)}
              </ul>
              {p.work.linkedin.claims.length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">Skills: {p.work.linkedin.claims.map((c) => c.name_raw).join(", ")}</p>
              )}
            </div>
          )}
          {p.work.resume_file && (
            <div>
              <h4 className="mb-2 text-sm font-medium">Resume</h4>
              {p.work.resume_file_kind === "pdf" ? (
                <iframe src={`${API}/candidates/${p.candidate_id}/files/resume`} title="Resume" className="h-[36rem] w-full rounded-lg border bg-white" />
              ) : (
                <a href={`${API}/candidates/${p.candidate_id}/files/resume`} className="text-sm text-primary hover:underline">Download the resume ({p.work.resume_file_kind})</a>
              )}
            </div>
          )}
        </div>
      </Section>
    </article>
  );
}

function Stat({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border bg-white px-3 py-2">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}{hint && <span title={hint} aria-label={hint}><Info className="size-3" /></span>}
      </div>
      <div className={cn("text-2xl font-semibold tabular-nums", accent && "text-primary")}>{value}</div>
      {hint && label === "Evidence confidence" && <p className="text-[11px] leading-tight text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Section({ title, badge, action, children }: { title: string; badge?: string | null; action?: React.ReactNode;
  children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-base font-semibold">{title}</h3>
        {badge && <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">{badge}</Badge>}
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}
