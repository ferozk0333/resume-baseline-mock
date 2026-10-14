"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { get, type Baseline, type Criterion } from "@/lib/api";

const REACH = ["", "Own work", "Team", "Cross-functional", "Organization", "External"];
const selectClass = "h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm";

export const EMPTY_BASELINE: Baseline = { title: "", level: "entry", required_years: 1, expected_reach: 2,
  scope_expectation: "", complexity_criteria: "", description: "", is_mock: true, criteria: [] };

export function BaselineForm({ initial, onSubmit, submitLabel }: {
  initial: Baseline; onSubmit: (b: Baseline) => Promise<void>; submitLabel: string;
}) {
  const [b, setB] = useState<Baseline>(initial);
  const [warning, setWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {            // non-blocking level/years warning, updated as you type (specs/06 §1)
    const t = setTimeout(() => {
      if (!b.level.trim()) return setWarning(null);
      get<{ warning: string | null }>(`/baselines-check?level=${encodeURIComponent(b.level)}&required_years=${b.required_years}`)
        .then((r) => setWarning(r.warning)).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [b.level, b.required_years]);

  const setCrit = (i: number, c: Partial<Criterion>) =>
    setB({ ...b, criteria: b.criteria.map((x, j) => (j === i ? { ...x, ...c } : x)) });

  return (
    <form className="space-y-6" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try { await onSubmit(b); } finally { setBusy(false); }
    }}>
      <Card>
        <CardHeader><CardTitle className="text-base">Role</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <F id="title" label="Title"><Input id="title" required value={b.title} onChange={(e) => setB({ ...b, title: e.target.value })} /></F>
          <F id="reach" label="Expected reach of impact">
            <select id="reach" className={selectClass} value={b.expected_reach}
              onChange={(e) => setB({ ...b, expected_reach: Number(e.target.value) })}>
              {REACH.slice(1).map((r, i) => <option key={r} value={i + 1}>{i + 1} · {r}</option>)}
            </select>
          </F>
          <F id="level" label="Level"><Input id="level" required value={b.level} onChange={(e) => setB({ ...b, level: e.target.value })} /></F>
          <F id="years" label="Required years"><Input id="years" type="number" min={0} step={0.5} required value={b.required_years}
            onChange={(e) => setB({ ...b, required_years: Number(e.target.value) })} /></F>
          {warning && (
            <Alert className="md:col-span-2 border-amber-300 bg-amber-50 text-amber-900">
              <AlertTriangle /><AlertDescription className="text-amber-900">{warning}</AlertDescription>
            </Alert>
          )}
          <div className="md:col-span-2"><F id="desc" label="Description (short job description)">
            <Textarea id="desc" value={b.description ?? ""} onChange={(e) => setB({ ...b, description: e.target.value })} /></F></div>
          <F id="scope" label="Scope expectation"><Textarea id="scope" value={b.scope_expectation ?? ""}
            onChange={(e) => setB({ ...b, scope_expectation: e.target.value })} /></F>
          <F id="complexity" label="Complexity criteria"><Textarea id="complexity" value={b.complexity_criteria ?? ""}
            onChange={(e) => setB({ ...b, complexity_criteria: e.target.value })} /></F>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Criteria</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="hidden grid-cols-[7rem_1fr_1fr_1fr_7rem_2rem] gap-2 text-xs font-medium text-muted-foreground md:grid">
            <span>Kind</span><span>Name</span><span>Description</span><span>Aliases (comma-separated)</span><span>Priority</span><span />
          </div>
          {b.criteria.map((c, i) => (
            <div key={i} className="grid gap-2 md:grid-cols-[7rem_1fr_1fr_1fr_7rem_2rem]">
              <select aria-label={`Criterion ${i + 1} kind`} className={selectClass} value={c.kind}
                onChange={(e) => setCrit(i, { kind: e.target.value as Criterion["kind"] })}>
                <option value="skill">skill</option><option value="tool">tool</option><option value="certification">certification</option>
              </select>
              <Input aria-label={`Criterion ${i + 1} name`} required value={c.name} onChange={(e) => setCrit(i, { name: e.target.value })} />
              <Input aria-label={`Criterion ${i + 1} description`} value={c.description ?? ""}
                onChange={(e) => setCrit(i, { description: e.target.value })} />
              <Input aria-label={`Criterion ${i + 1} aliases`} value={c.aliases.join(", ")}
                onChange={(e) => setCrit(i, { aliases: e.target.value.split(",").map((a) => a.trim()).filter(Boolean) })} />
              <select aria-label={`Criterion ${i + 1} priority`} className={selectClass} value={c.priority}
                onChange={(e) => setCrit(i, { priority: e.target.value as Criterion["priority"] })}>
                <option value="required">required</option><option value="preferred">preferred</option>
              </select>
              <Button type="button" variant="ghost" size="icon" aria-label={`Remove criterion ${i + 1}`}
                onClick={() => setB({ ...b, criteria: b.criteria.filter((_, j) => j !== i) })}><Trash2 /></Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={() => setB({ ...b, criteria: [...b.criteria,
            { kind: "skill", name: "", description: "", aliases: [], priority: "required" }] })}><Plus /> Add a criterion</Button>
        </CardContent>
      </Card>
      <Button type="submit" size="lg" disabled={busy}>{submitLabel}</Button>
    </form>
  );
}

function F({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>;
}
