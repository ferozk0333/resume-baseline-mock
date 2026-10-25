"use client";
import { LinkedText } from "@/components/shared/ext-link";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, Check, Cloud, GripVertical, Info, Loader2, Plus, Quote, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PostingPreview } from "@/components/features/jobs/posting-preview";
import { ApiError, DIMENSIONS, del, get, post, put, REACH, titleCase, WORKPLACE, type JobDraft, type JobForm,
  type Baseline, type ReqRow } from "@/lib/api";
import { cn } from "@/lib/utils";

const STEPS = ["Start", "Role & requirements", "Hiring setup", "Review & publish"];
const PRESETS = ["Startup", "Enterprise", "Proof-first"];
const field = "h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm";

export function Stepper({ step, onStep }: { step: number; onStep?: (s: number) => void }) {
  return (
    <ol aria-label="Steps" className="flex flex-wrap items-center gap-2 text-sm">
      {STEPS.map((s, i) => {
        const n = i + 1;
        const can = onStep && n >= 2 && n !== step;
        return (
          <li key={s} className="flex items-center gap-2">
            <button type="button" disabled={!can} onClick={() => can && onStep!(n)} aria-current={n === step ? "step" : undefined}
              className={cn("flex items-center gap-2 rounded-full px-2.5 py-1", n === step ? "bg-primary text-primary-foreground"
                : n < step ? "text-foreground" : "text-muted-foreground", can && "hover:bg-muted")}>
              <span className={cn("grid size-5 place-items-center rounded-full border text-xs tabular-nums",
                n === step && "border-primary-foreground", n < step && "border-primary bg-primary text-primary-foreground")}>
                {n < step ? <Check className="size-3" /> : n}
              </span>{s}
            </button>
            {i < STEPS.length - 1 && <span className="h-px w-6 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

function FromJd({ quote }: { quote?: string | null }) {
  if (!quote) return null;
  return <Badge variant="outline" title={quote} className="cursor-help border-sky-200 bg-sky-50 text-[10px] text-sky-800">
    <Quote className="size-2.5" /> From JD</Badge>;
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="flex gap-1 text-xs text-muted-foreground"><Info className="mt-0.5 size-3 shrink-0" />{children}</p>;
}

/** Steps 2–4 of Create job, and Edit job. Every change is autosaved; "Save as draft" is always there. */
export function JobFormPage({ jobId }: { jobId: string }) {
  const router = useRouter();
  const [form, setForm] = useState<JobForm | null>(null);
  const [saved, setSaved] = useState<"saved" | "saving" | "error">("saved");
  const [impact, setImpact] = useState<{ applicants: number; calls: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<JobDraft | null>(null);
  // Saves run one after another, and flush() resolves only once the last one has landed: Publish never reaches the
  // server ahead of a save that a timer already started.
  const saving = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => { get<JobForm>(`/jobs/${jobId}/draft`).then(setForm).catch((e) => toast.error(e.message)); }, [jobId]);

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const d = pending.current;
    pending.current = null;
    saving.current = saving.current.then(async () => {
      if (!d) return;
      setSaved("saving");
      try {
        const f = await put<JobForm>(`/jobs/${jobId}/draft`, { draft: d });
        // The role family follows the title until the recruiter picks one: take the server's suggestion.
        setForm((cur) => (cur ? { ...cur, warnings: f.warnings, draft: cur.draft.details.role_family_auto === false ? cur.draft
          : { ...cur.draft, details: { ...cur.draft.details, role_family: f.draft.details.role_family, role_family_auto: true } } } : f));
        setSaved("saved");
      } catch (e) { setSaved("error"); toast.error((e as Error).message); }
    });
    return saving.current;
  }, [jobId]);
  useEffect(() => () => { void flush(); }, [flush]);

  const update = useCallback((fn: (d: JobDraft) => JobDraft) => {
    setForm((cur) => {
      if (!cur) return cur;
      const draft = fn(structuredClone(cur.draft));
      pending.current = draft;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { void flush(); }, 500);
      setSaved("saving");
      return { ...cur, draft };
    });
  }, [flush]);

  if (!form) return <div className="h-96 animate-pulse rounded-xl bg-muted" />;
  const d = form.draft;
  const editing = form.status !== "draft";
  const step = Math.min(4, Math.max(2, d.step || 2));
  const goto = (s: number) => update((x) => ({ ...x, step: s }));

  async function saveDraft() {
    await flush();
    toast.success(editing ? "Edit saved as a draft (the live job is unchanged until you save)" : "Saved as draft");
    router.push("/recruiter");
  }
  async function publish(confirm = false) {
    setBusy(true);
    await flush();
    try {
      if (!editing) {
        await post(`/jobs/${jobId}/publish`);
        toast.success("Job published");
      } else {
        const out = await post<{ rescoring: number }>(`/jobs/${jobId}/save`, { confirm });
        toast.success(out.rescoring ? `Saved; re-scoring ${out.rescoring} applicant${out.rescoring === 1 ? "" : "s"}` : "Saved");
      }
      router.push(`/recruiter/jobs/${jobId}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try { setImpact(JSON.parse(e.message)); } catch { toast.error(e.message); }
      } else toast.error((e as Error).message);
    } finally {
      setBusy(false);                    // the router may keep this page's state when the recruiter comes back
    }
  }
  async function discard() {
    await del(`/jobs/${jobId}/draft`);
    router.push(editing ? `/recruiter/jobs/${jobId}` : "/recruiter");
  }

  const showJd = !!form.jd_text && step === 2;
  return (
    <div className="space-y-5 pb-24">
      <div className="space-y-3">
        <Link href={editing ? `/recruiter/jobs/${jobId}` : "/recruiter"} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> {editing ? "Back to the job" : "Jobs"}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{editing ? `Edit job · ${d.details.title}` : d.details.title || "Create job"}</h1>
          <span className="flex items-center gap-1 text-xs text-muted-foreground" aria-live="polite">
            {saved === "saving" ? <><Loader2 className="size-3 animate-spin" /> Saving…</> : saved === "saved"
              ? <><Cloud className="size-3" /> {editing ? "Edit saved as a draft" : "Draft saved"}</> : "Not saved"}
          </span>
        </div>
        <Stepper step={step} onStep={goto} />
      </div>
      {d.extraction?.status === "unavailable" && step === 2 && (
        <p className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900"><AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {d.extraction.notice}</p>
      )}
      {d.extraction?.status === "ok" && step === 2 && (
        <p className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-900">
          Read from the job description.
          {(d.extraction.dropped?.length ?? 0) > 0 && <> {d.extraction.dropped!.length} item{d.extraction.dropped!.length === 1 ? " was" : "s were"} left out
            ({d.extraction.dropped!.map((x) => `${x.text}: ${x.reason}`).join("; ")}).</>}
        </p>
      )}

      <div className="min-w-0 space-y-5">
        {step === 2 && <StepRole d={d} update={update} aside={showJd ? (
          <aside aria-label="Job description" className="rounded-xl border bg-white p-4">
            <h2 className="mb-2 text-sm font-semibold">Job description</h2>
            <pre className="max-h-[38rem] overflow-y-auto font-[inherit] text-xs leading-relaxed whitespace-pre-wrap"><LinkedText text={form.jd_text} /></pre>
          </aside>) : null} />}
        {step === 3 && <StepSetup d={d} update={update} />}
        {step === 4 && <StepReview form={form} goto={goto} />}
      </div>

      <footer className="fixed inset-x-0 bottom-0 z-20 border-t bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-2 px-6 py-3">
          <Button variant="outline" disabled={step <= 2} onClick={() => goto(step - 1)}>Back</Button>
          <Button variant="ghost" onClick={saveDraft}>Save as draft</Button>
          {editing && <Button variant="ghost" onClick={discard}>Discard changes</Button>}
          <div className="ml-auto flex gap-2">
            {step < 4 ? <Button onClick={() => goto(step + 1)}>Next</Button> : (
              <Button disabled={busy} onClick={() => publish(false)}>
                {busy && <Loader2 className="animate-spin" />}{editing ? "Save changes" : "Publish"}
              </Button>
            )}
          </div>
        </div>
      </footer>

      <Dialog open={!!impact} onOpenChange={(o) => !o && setImpact(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Re-score applicants?</DialogTitle>
            <DialogDescription>
              {impact?.applicants} applicant{impact?.applicants === 1 ? "" : "s"} will be re-scored.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImpact(null)}>Cancel</Button>
            <Button onClick={() => { setImpact(null); publish(true); }}>Save and re-score</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type Upd = (fn: (d: JobDraft) => JobDraft) => void;

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3 rounded-xl border bg-white p-5">
      <div><h2 className="font-semibold">{title}</h2>{hint && <p className="text-sm text-muted-foreground">{hint}</p>}</div>
      {children}
    </section>
  );
}

function StepRole({ d, update, aside }: { d: JobDraft; update: Upd; aside: React.ReactNode }) {
  const det = d.details;
  const set = (patch: Partial<JobDraft["details"]>) => update((x) => ({ ...x, details: { ...x.details, ...patch } }));
  const q = d.detail_quotes ?? {};
  const required = d.requirements.filter((r) => r.priority === "required" && r.name.trim()).length;
  const [families, setFamilies] = useState<Baseline[]>([]);
  useEffect(() => { get<Baseline[]>("/baselines").then(setFamilies).catch(() => setFamilies([])); }, []);
  const text = (key: keyof JobDraft["details"], label: string, star = false, placeholder = "") => (
    <div className="space-y-1">
      <Label htmlFor={key} className="flex items-center gap-1.5">{label}{star && <span className="text-red-600">*</span>}<FromJd quote={q[key]} /></Label>
      <Input id={key} value={(det[key] as string) ?? ""} placeholder={placeholder} onChange={(e) => set({ [key]: e.target.value })} className="bg-white" />
    </div>
  );
  return (
    <>
      <div className={cn("grid gap-5", aside && "xl:grid-cols-[minmax(0,1fr)_26rem]")}>
      <Section title="Details">
        <div className="grid gap-3 md:grid-cols-2">
          {text("title", "Title", true, "e.g. AI Engineer")}
          {text("company", "Company", true)}
          {text("department", "Department")}
          {text("location", "Location", false, "e.g. Austin, TX")}
          <div className="space-y-1">
            <Label htmlFor="workplace" className="flex items-center gap-1.5">Workplace<FromJd quote={q.workplace} /></Label>
            <select id="workplace" value={det.workplace ?? ""} onChange={(e) => set({ workplace: (e.target.value || null) as JobDraft["details"]["workplace"] })} className={field}>
              <option value="">Not stated</option>
              {Object.entries(WORKPLACE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="employment_type" className="flex items-center gap-1.5">Employment type<FromJd quote={q.employment_type} /></Label>
            <select id="employment_type" value={det.employment_type} onChange={(e) => set({ employment_type: e.target.value })} className={field}>
              {["Full-time", "Part-time", "Contract", "Internship", "Temporary"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="level" className="flex items-center gap-1.5">Level<span className="text-red-600">*</span><FromJd quote={q.level} /></Label>
            <select id="level" value={det.level} onChange={(e) => set({ level: e.target.value })} className={field}>
              {["entry", "mid", "senior", "lead"].map((l) => <option key={l} value={l}>{titleCase(l)}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="role_family">Role family</Label>
            <select id="role_family" value={det.role_family ?? "none"} className={field}
              onChange={(e) => set({ role_family: e.target.value, role_family_auto: false })}>
              {families.map((f) => <option key={f.id} value={f.id!}>{f.title}</option>)}
              <option value="none">None</option>
            </select>
            {det.role_family_auto !== false && <Hint>Suggested from the title.</Hint>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="min_years" className="flex items-center gap-1.5">Minimum years<span className="text-red-600">*</span><FromJd quote={q.min_years} /></Label>
            <Input id="min_years" type="number" min={0} step={0.5} value={det.min_years ?? ""} className="bg-white"
              onChange={(e) => set({ min_years: e.target.value === "" ? null : Number(e.target.value) })} />
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="short_description">Short description shown to candidates</Label>
            <Textarea id="short_description" value={det.short_description} onChange={(e) => set({ short_description: e.target.value })} className="min-h-16 bg-white text-sm" />
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="responsibilities">What you&apos;ll do (one per line, shown to candidates)</Label>
            <Textarea id="responsibilities" value={(det.responsibilities ?? []).join("\n")} className="min-h-20 bg-white text-sm"
              onChange={(e) => set({ responsibilities: e.target.value.split("\n") })} />
          </div>
        </div>
      </Section>
      {aside}
      </div>
      <Requirements d={d} update={update} required={required} />
    </>
  );
}

function Requirements({ d, update, required }: { d: JobDraft; update: Upd; required: number }) {
  const [drag, setDrag] = useState<string | null>(null);
  const setRow = (key: string, patch: Partial<ReqRow>) =>
    update((x) => ({ ...x, requirements: x.requirements.map((r) => (r.key === key ? { ...r, ...patch } : r)) }));
  const move = (from: number, to: number) => update((x) => {
    const rows = [...x.requirements];
    rows.splice(to, 0, rows.splice(from, 1)[0]);
    return { ...x, requirements: rows };
  });
  const add = () => update((x) => ({ ...x, requirements: [...x.requirements, { key: Math.random().toString(36).slice(2, 10),
    criterion_id: null, name: "", kind: "skill", priority: "required", must_have: false, aliases: [], suggested_aliases: [],
    description: "", from_jd: false, quote: null }] }));
  return (
    <Section title="Requirements">
      {required > 12 && (
        <p className="flex gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900"><AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {required} required items. Job descriptions are often wish lists: consider marking some as preferred.</p>
      )}
      <div className="overflow-x-auto">
        <table aria-label="Requirements" className="w-full min-w-[56rem] text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr><th className="w-6" /><th className="py-1">Name</th><th>Type</th><th>Required?</th><th className="text-center">Must-have</th>
              <th>Aliases</th><th>Short description</th><th className="w-8" /></tr>
          </thead>
          <tbody>
            {d.requirements.map((r, i) => (
              <tr key={r.key} draggable onDragStart={() => setDrag(r.key)} onDragEnd={() => setDrag(null)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => { const from = d.requirements.findIndex((x) => x.key === drag); if (drag && from !== i) move(from, i); }}
                className="border-t align-top">
                <td className="py-1.5"><GripVertical className="mt-2 size-4 cursor-grab text-muted-foreground" aria-hidden /></td>
                <td className="py-1.5 pr-2">
                  <Input aria-label={`Requirement ${i + 1} name`} value={r.name} onChange={(e) => setRow(r.key, { name: e.target.value })} className="h-8 min-w-40 bg-white" />
                  {r.from_jd && <div className="mt-0.5"><FromJd quote={r.quote} /></div>}
                </td>
                <td className="py-1.5 pr-2">
                  <select aria-label={`${r.name || "Requirement"} type`} value={r.kind} onChange={(e) => setRow(r.key, { kind: e.target.value as ReqRow["kind"] })} className={cn(field, "h-8 min-w-32")}>
                    <option value="skill">Skill</option><option value="tool">Tool</option><option value="certification">Certification</option>
                  </select>
                </td>
                <td className="py-1.5 pr-2">
                  <select aria-label={`${r.name || "Requirement"} priority`} value={r.priority}
                    onChange={(e) => setRow(r.key, { priority: e.target.value as ReqRow["priority"], must_have: e.target.value === "preferred" ? false : r.must_have })} className={cn(field, "h-8 min-w-28")}>
                    <option value="required">Required</option><option value="preferred">Preferred</option>
                  </select>
                </td>
                <td className="py-1.5 text-center">
                  <input type="checkbox" aria-label={`${r.name || "Requirement"} must-have`} className="mt-2.5" checked={r.must_have}
                    onChange={(e) => setRow(r.key, { must_have: e.target.checked })} />
                </td>
                <td className="py-1.5 pr-2"><Aliases r={r} onChange={(aliases) => setRow(r.key, { aliases })} /></td>
                <td className="py-1.5 pr-2">
                  <Input aria-label={`${r.name || "Requirement"} description`} value={r.description} onChange={(e) => setRow(r.key, { description: e.target.value })} className="h-8 min-w-40 bg-white" />
                </td>
                <td className="py-1.5">
                  <div className="flex">
                    <Button size="icon-xs" variant="ghost" aria-label={`Move ${r.name} up`} disabled={i === 0} onClick={() => move(i, i - 1)}><ArrowUp /></Button>
                    <Button size="icon-xs" variant="ghost" aria-label={`Move ${r.name} down`} disabled={i === d.requirements.length - 1} onClick={() => move(i, i + 1)}><ArrowDown /></Button>
                    <Button size="icon-xs" variant="ghost" aria-label={`Delete ${r.name || "requirement"}`}
                      onClick={() => update((x) => ({ ...x, requirements: x.requirements.filter((y) => y.key !== r.key) }))}><Trash2 /></Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.requirements.length === 0 && <p className="text-sm text-muted-foreground">No requirements yet.</p>}
      <Button size="sm" variant="outline" onClick={add}><Plus /> Add requirement</Button>
    </Section>
  );
}

function Aliases({ r, onChange }: { r: ReqRow; onChange: (a: string[]) => void }) {
  const [v, setV] = useState("");
  const suggested = new Set(r.suggested_aliases ?? []);
  return (
    <div className="flex min-w-48 flex-wrap items-center gap-1">
      {r.aliases.map((a) => (
        <span key={a} title={suggested.has(a) ? "Suggested from the job description" : undefined}
          className={cn("inline-flex items-center gap-0.5 rounded-md border px-1.5 py-0.5 text-xs", suggested.has(a) && "border-dashed border-sky-300 bg-sky-50")}>
          {a}{suggested.has(a) && <span className="text-[9px] text-sky-700"> Suggested</span>}
          <button type="button" aria-label={`Remove alias ${a}`} onClick={() => onChange(r.aliases.filter((x) => x !== a))}><X className="size-3" /></button>
        </span>
      ))}
      <input aria-label={`Add alias to ${r.name || "requirement"}`} value={v} placeholder="+ alias" className="h-7 w-20 rounded-md border border-field-border bg-field px-1.5 text-xs"
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && v.trim()) { e.preventDefault(); onChange([...r.aliases, v.trim()]); setV(""); } }} />
    </div>
  );
}

function StepSetup({ d, update }: { d: JobDraft; update: Upd }) {
  const g = d.good;
  const setGood = (patch: Partial<JobDraft["good"]>) => update((x) => ({ ...x, good: { ...x.good, ...patch } }));
  const setPlan = (patch: Partial<JobDraft["plan"]>) => update((x) => ({ ...x, plan: { ...x.plan, ...patch } }));
  const [advanced, setAdvanced] = useState(false);
  const [newStage, setNewStage] = useState("");
  const dims = d.ranking.weights.dimensions;
  const total = DIMENSIONS.reduce((s, k) => s + (dims[k] ?? 0), 0);
  const stages = d.plan.stages;
  const q = d.good_quotes ?? {};
  return (
    <>
      <Section title="What good looks like">
        <div className="space-y-1">
          <Label htmlFor="scope" className="flex items-center gap-1.5">What will this person own?<FromJd quote={q.scope_expectation} /></Label>
          <Textarea id="scope" value={g.scope_expectation} onChange={(e) => setGood({ scope_expectation: e.target.value })} className="min-h-16 bg-white text-sm"
            placeholder="e.g. Well-defined components end to end, such as a retrieval pipeline." />
        </div>
        <div className="space-y-1">
          <Label htmlFor="complexity" className="flex items-center gap-1.5">What makes the work hard?<FromJd quote={q.complexity_criteria} /></Label>
          <Textarea id="complexity" value={g.complexity_criteria} onChange={(e) => setGood({ complexity_criteria: e.target.value })} className="min-h-16 bg-white text-sm"
            placeholder="e.g. Answer quality and cost on messy documents in production." />
        </div>
        <div className="space-y-1">
          <Label htmlFor="reach" className="flex items-center gap-1.5">How far does their work reach?<FromJd quote={q.expected_reach} /></Label>
          <select id="reach" value={g.expected_reach ?? ""} onChange={(e) => setGood({ expected_reach: e.target.value ? Number(e.target.value) : null })} className={cn(field, "max-w-xs")}>
            <option value="">Not set (team)</option>
            {Object.entries(REACH).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
      </Section>
      <Section title="Ranking emphasis">
        <div role="radiogroup" aria-label="Ranking preset" className="flex flex-wrap gap-2">
          {[null, ...PRESETS].map((p) => (
            <button key={p ?? "team"} type="button" role="radio" aria-checked={d.ranking.preset === p}
              onClick={() => update((x) => ({ ...x, ranking: { ...x.ranking, preset: p } }))}
              className={cn("rounded-lg border px-3 py-2 text-left text-sm", d.ranking.preset === p ? "border-primary bg-primary/5 ring-2 ring-primary/30" : "bg-white hover:bg-muted")}>
              {p ?? "Team table (default)"}
            </button>
          ))}
        </div>
        <button type="button" className="text-sm text-primary hover:underline" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
          {advanced ? "Hide" : "Advanced"}: dimension weights</button>
        {advanced && (
          <div className="grid gap-3 md:grid-cols-2">
            {DIMENSIONS.map((k) => (
              <label key={k} className="space-y-1 text-sm">
                <span className="flex justify-between"><span>{titleCase(k)}</span><span className="tabular-nums">{Math.round(dims[k])}%</span></span>
                <input type="range" min={0} max={60} step={5} value={dims[k]} aria-label={`${titleCase(k)} weight`} className="w-full"
                  onChange={(e) => update((x) => ({ ...x, ranking: { preset: null, weights: { ...x.ranking.weights,
                    dimensions: { ...x.ranking.weights.dimensions, [k]: Number(e.target.value) } } } }))} />
              </label>
            ))}
            {Math.round(total) !== 100 && <p className="text-xs text-amber-700 md:col-span-2">Weights add up to {Math.round(total)}%; they are used in proportion.</p>}
          </div>
        )}
      </Section>
      <Section title="Interview plan">
        <ol className="space-y-1.5">
          {stages.map((s, i) => (
            <li key={`${s}-${i}`} className="flex items-center gap-2">
              <span className="w-5 text-xs tabular-nums text-muted-foreground">{i + 1}</span>
              <Input aria-label={`Stage ${i + 1}`} defaultValue={s} className="h-8 w-64 bg-white"
                onBlur={(e) => e.target.value.trim() && e.target.value !== s && setPlan({ stages: stages.map((x, j) => (j === i ? e.target.value.trim() : x)) })} />
              <Button size="icon-xs" variant="ghost" aria-label={`Move ${s} up`} disabled={i === 0}
                onClick={() => { const n = [...stages]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setPlan({ stages: n }); }}><ArrowUp /></Button>
              <Button size="icon-xs" variant="ghost" aria-label={`Move ${s} down`} disabled={i === stages.length - 1}
                onClick={() => { const n = [...stages]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; setPlan({ stages: n }); }}><ArrowDown /></Button>
              <Button size="icon-xs" variant="ghost" aria-label={`Remove ${s}`} disabled={stages.length <= 1}
                onClick={() => setPlan({ stages: stages.filter((_, j) => j !== i) })}><Trash2 /></Button>
            </li>
          ))}
          <li className="pl-7 text-xs text-muted-foreground">Then: Not moving forward (fixed)</li>
        </ol>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (newStage.trim()) { setPlan({ stages: [...stages, newStage.trim()] }); setNewStage(""); } }}>
          <Input aria-label="New stage" placeholder="e.g. Take-home task" value={newStage} onChange={(e) => setNewStage(e.target.value)} className="h-8 w-64 bg-white" />
          <Button size="sm" variant="outline" type="submit"><Plus /> Add stage</Button>
        </form>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1"><Label htmlFor="duration">Interview duration (minutes)</Label>
            <Input id="duration" type="number" min={5} max={240} value={d.plan.duration_minutes} className="bg-white"
              onChange={(e) => setPlan({ duration_minutes: Number(e.target.value) })} /></div>
          <div className="space-y-1"><Label htmlFor="recruiter">Recruiter name</Label>
            <Input id="recruiter" value={d.plan.recruiter_name} onChange={(e) => setPlan({ recruiter_name: e.target.value })} className="bg-white" /></div>
          <div className="space-y-1"><Label htmlFor="availability">Availability windows</Label>
            <Input id="availability" value={d.plan.availability} placeholder="Mon–Wed 10:00–14:00 ET" onChange={(e) => setPlan({ availability: e.target.value })} className="bg-white" />
            </div>
          <div className="space-y-1"><Label htmlFor="link">Or a scheduling link</Label>
            <Input id="link" value={d.plan.scheduling_link} placeholder="https://…" onChange={(e) => setPlan({ scheduling_link: e.target.value })} className="bg-white" /></div>
        </div>
      </Section>
    </>
  );
}

function StepReview({ form, goto }: { form: JobForm; goto: (s: number) => void }) {
  const d = form.draft;
  const reqs = d.requirements.filter((r) => r.name.trim());
  const posting = { ...d.details, required: reqs.filter((r) => r.priority === "required").map((r) => r.name),
    preferred: reqs.filter((r) => r.priority === "preferred").map((r) => r.name),
    responsibilities: (d.details.responsibilities ?? []).filter((x) => x.trim()) };
  const block = (title: string, s: number, children: React.ReactNode) => (
    <section aria-label={`${title} summary`} className="rounded-xl border bg-white p-4">
      <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">{title}</h2>
        <Button size="xs" variant="outline" onClick={() => goto(s)}>Edit</Button></div>
      <div className="text-sm">{children}</div>
    </section>
  );
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="space-y-3">
        {form.warnings.length > 0 && (
          <ul aria-label="Warnings" className="space-y-1 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
            {form.warnings.map((w) => <li key={w} className="flex gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{w}</li>)}
          </ul>
        )}
        {block("Details", 2, <p>{d.details.title || "—"} · {d.details.company || "—"} · {titleCase(d.details.level)} · {d.details.min_years ?? "—"}+ years
          {d.details.location && ` · ${d.details.location}`}{d.details.workplace && ` · ${WORKPLACE[d.details.workplace]}`}</p>)}
        {block("Requirements", 2, <p>{reqs.filter((r) => r.priority === "required").length} required · {reqs.filter((r) => r.priority === "preferred").length} preferred ·
          must-haves: {reqs.filter((r) => r.must_have).map((r) => r.name).join(", ") || "none"}</p>)}
        {block("What good looks like", 3, <ul className="space-y-1">
          <li><span className="text-muted-foreground">Owns:</span> {d.good.scope_expectation || "—"}</li>
          <li><span className="text-muted-foreground">Hard because:</span> {d.good.complexity_criteria || "—"}</li>
          <li><span className="text-muted-foreground">Reach:</span> {d.good.expected_reach ? REACH[d.good.expected_reach] : "Not set (team)"}</li></ul>)}
        {block("Ranking and interview plan", 3, <p>{d.ranking.preset ?? "Team table"} · {d.plan.stages.join(" → ")} · {d.plan.duration_minutes} min ·
          {" "}{d.plan.scheduling_link || d.plan.availability}</p>)}
      </div>
      <section className="h-fit rounded-xl border bg-white p-5">
        <h2 className="mb-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Candidate preview</h2>
        <PostingPreview p={posting} />
      </section>
    </div>
  );
}
