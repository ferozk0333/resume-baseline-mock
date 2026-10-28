"use client";
import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowRight, ArrowUp, History, Info, Loader2, Minus, RefreshCw, Sparkles, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { get, post, titleCase, type Job, type SuggestResult, type SuggestionSummary, type WeightChange,
  type WeightPreview, type WeightSuggestion } from "@/lib/api";

const MAX = 800;
const PLACEHOLDER = "e.g. Hands-on builders who have shipped real projects; certifications with a credential link matter more than years in the job.";
const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** Recruiter weight assistant (specs/21): describe the role → a suggestion with its effect on the ranking → Apply
 * (or Discard). Nothing changes until Apply; Undo restores the previous weights. */
export function WeightAssistant({ job, onJob }: { job: Job; onJob: (j: Job) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [s, setS] = useState<WeightSuggestion | null>(null);
  const [history, setHistory] = useState<SuggestionSummary[]>([]);
  const [applied, setApplied] = useState<string | null>(null);      // suggestion id that can be undone

  const loadHistory = useCallback(() => {
    get<SuggestionSummary[]>(`/jobs/${job.id}/weight-suggestions`).then(setHistory).catch(() => {});
  }, [job.id]);
  useEffect(loadHistory, [loadHistory]);

  async function suggest(fresh = false) {
    setBusy(true); setProblem(null);
    try {
      const r = await post<SuggestResult>(`/jobs/${job.id}/weight-suggestions`, { text, fresh });
      if (r.status === "ok") { setS(r.suggestion); setApplied(null); }
      else { setS(null); setProblem(r.message); }
      loadHistory();
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  }

  async function reopen(id: string) {
    try {
      const v = await get<WeightSuggestion>(`/jobs/${job.id}/weight-suggestions/${id}`);
      setS(v); setText(v.text); setProblem(null); setApplied(null);
    } catch (e) { toast.error((e as Error).message); }
  }

  async function undo(id: string) {
    try {
      const r = await post<{ job: Job }>(`/jobs/${job.id}/weight-suggestions/${id}/undo`);
      onJob(r.job); setApplied(null); loadHistory();
      toast.success("Previous weights restored.");
    } catch (e) { toast.error((e as Error).message); }
  }

  async function apply() {
    if (!s) return;
    try {
      const r = await post<{ job: Job }>(`/jobs/${job.id}/weight-suggestions/${s.id}/apply`);
      onJob(r.job); setApplied(s.id); setS(null); loadHistory();
      const id = s.id;
      toast.success("Weights applied. Applicants are re-ranked.", { action: { label: "Undo", onClick: () => undo(id) } });
    } catch (e) { toast.error((e as Error).message); }
  }

  return (
    <section aria-labelledby="wa-title" className="space-y-3 rounded-lg border bg-muted/20 p-4">
      <div>
        <h3 id="wa-title" className="flex items-center gap-1.5 text-sm font-medium"><Sparkles className="size-4 text-primary" /> Suggest weights from a description</h3>
        <p className="text-xs text-muted-foreground">Weights only. You review the suggestion and its effect on the ranking before anything changes.</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="wa-text">Describe what you&apos;re looking for in this role</Label>
        <Textarea id="wa-text" value={text} maxLength={MAX} rows={3} placeholder={PLACEHOLDER} className="bg-white"
          onChange={(e) => setText(e.target.value)} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className={cn("text-xs tabular-nums", text.length > MAX - 50 ? "text-amber-800" : "text-muted-foreground")}>
            {text.length}/{MAX}</span>
          <Button size="sm" onClick={() => suggest(false)} disabled={busy || !text.trim()}>
            {busy ? <><Loader2 className="animate-spin" /> Suggesting…</> : <><Sparkles /> Suggest weights</>}
          </Button>
        </div>
      </div>

      {problem && (
        <p role="status" className="flex items-start gap-2 rounded-md border bg-white px-3 py-2 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />{problem}
        </p>
      )}

      {applied && !s && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Weights applied. Applicants are re-ranked.
          <Button size="sm" variant="outline" className="ml-auto bg-white" onClick={() => undo(applied)}><Undo2 /> Undo</Button>
        </div>
      )}

      {s && <Suggestion s={s} busy={busy} onApply={apply} onDiscard={() => setS(null)} onAgain={() => suggest(true)} />}

      {history.length > 0 && (
        <details className="group text-sm" data-testid="wa-history">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
            <History className="size-3.5" /> Recent suggestions ({history.length})
          </summary>
          <ul className="mt-2 divide-y rounded-md border bg-white">
            {history.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center gap-2 px-3 py-1.5">
                <span className="w-24 shrink-0 text-xs tabular-nums text-muted-foreground">{new Date(h.created_at).toLocaleDateString()}</span>
                <span className="min-w-0 flex-1 truncate" title={h.text}>{h.text}</span>
                <Badge variant={h.applied ? "default" : "outline"} className="text-[10px]">
                  {h.applied ? "Applied" : h.undone ? "Undone" : "Not applied"}</Badge>
                <Button size="xs" variant="ghost" onClick={() => reopen(h.id)}>Open</Button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function Suggestion({ s, busy, onApply, onDiscard, onAgain }: { s: WeightSuggestion; busy: boolean; onApply: () => void;
  onDiscard: () => void; onAgain: () => void }) {
  return (
    <article aria-label="Suggested weights" className="space-y-4 rounded-lg border bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <Badge variant="secondary" className="gap-1"><Sparkles className="size-3" />{s.label}</Badge>
          {s.summary && <p className="max-w-prose text-sm">{s.summary}</p>}
        </div>
        <Button size="sm" variant="ghost" onClick={onAgain} disabled={busy}><RefreshCw /> Suggest again</Button>
      </div>
      {s.ignored_notice && (
        <p role="note" data-testid="wa-ignored" className="flex items-start gap-2 rounded-md bg-muted px-3 py-2 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />{s.ignored_notice}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Changes changes={s.changes} unchanged={s.unchanged} />
        <Preview p={s.preview} />
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <Button onClick={onApply}>Apply weights</Button>
        <Button variant="ghost" onClick={onDiscard}>Discard</Button>
        <span className="text-xs text-muted-foreground">Nothing changes until you apply. You can undo afterwards.</span>
      </div>
    </article>
  );
}

function Change({ c }: { c: WeightChange }) {
  const up = c.suggested > c.current;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-1.5">
      <span className={cn("text-sm", !c.dimension && "font-medium")}>{c.label}</span>
      <span className="flex items-center gap-1 text-sm tabular-nums">
        <span className="text-muted-foreground">{fmt(c.current)}</span>
        <ArrowRight className="size-3 text-muted-foreground" aria-label="to" />
        <span className={cn("font-medium", up ? "text-emerald-700" : "text-amber-800")}>{fmt(c.suggested)}</span>
        {up ? <ArrowUp className="size-3.5 text-emerald-700" aria-label="up" /> : <ArrowDown className="size-3.5 text-amber-800" aria-label="down" />}
      </span>
      {c.reason && <span className="col-span-2 text-xs text-muted-foreground">{c.reason}</span>}
    </li>
  );
}

function Changes({ changes, unchanged }: { changes: WeightChange[]; unchanged: WeightChange[] }) {
  const dims = changes.filter((c) => !c.dimension);
  const groups = Array.from(new Set(changes.filter((c) => c.dimension).map((c) => c.dimension!)));
  return (
    <div className="space-y-3" data-testid="wa-changes">
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">What changes (out of 100 per group)</h4>
      {changes.length === 0 && <p className="text-sm text-muted-foreground">No weight changes.</p>}
      {dims.length > 0 && <div><p className="text-xs text-muted-foreground">Dimensions</p><ul className="divide-y">{dims.map((c) => <Change key={c.key} c={c} />)}</ul></div>}
      {groups.map((g) => (
        <div key={g}><p className="text-xs text-muted-foreground">Within {titleCase(g)}</p>
          <ul className="divide-y">{changes.filter((c) => c.dimension === g).map((c) => <Change key={c.key} c={c} />)}</ul></div>
      ))}
      {unchanged.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">No change ({unchanged.length})</summary>
          <ul className="mt-1 grid gap-x-4 sm:grid-cols-2">
            {unchanged.map((c) => <li key={c.key} className="flex justify-between py-0.5 text-xs"><span>{c.label}</span>
              <span className="tabular-nums text-muted-foreground">{fmt(c.current)}</span></li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function Move({ n }: { n: number }) {
  if (n === 0) return <span className="inline-flex items-center text-xs text-muted-foreground"><Minus className="size-3" aria-label="no change" /></span>;
  return (
    <span className={cn("inline-flex items-center text-xs font-medium tabular-nums", n > 0 ? "text-emerald-700" : "text-amber-800")}>
      {n > 0 ? <ArrowUp className="size-3" aria-label="up" /> : <ArrowDown className="size-3" aria-label="down" />}{Math.abs(n)}
    </span>
  );
}

function Preview({ p }: { p: WeightPreview }) {
  return (
    <div className="space-y-2" data-testid="wa-preview">
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Ranking preview</h4>
      {!p.available ? <p className="text-sm text-muted-foreground">{p.message}</p> : (
        <>
          <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            <span className="text-emerald-700">↑ {p.up} move up</span>
            <span className="text-amber-800">↓ {p.down} move down</span>
            <span className="text-muted-foreground">{p.same} unchanged · {p.total} scored</span>
          </p>
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-1 pr-2 font-medium">#</th><th className="py-1 pr-2 font-medium">Applicant</th>
              <th className="py-1 pr-2 font-medium">Move</th><th className="py-1 text-right font-medium">Match</th></tr></thead>
            <tbody>
              {p.top.map((r) => (
                <tr key={r.application_id} className="border-b last:border-b-0">
                  <td className="py-1 pr-2 tabular-nums">{r.rank}</td>
                  <td className="max-w-[12rem] truncate py-1 pr-2" title={r.name}>{r.name}</td>
                  <td className="py-1 pr-2" title={`was #${r.was}`}><Move n={r.move} /></td>
                  <td className="py-1 text-right text-xs tabular-nums whitespace-nowrap">
                    <span className="text-muted-foreground">{r.match_before.toFixed(1)} → </span>{r.match.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted-foreground">From the saved scores: no one is scored again.</p>
        </>
      )}
    </div>
  );
}
