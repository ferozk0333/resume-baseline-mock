"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Calculator } from "lucide-react";
import { cn } from "@/lib/utils";
import { get, post, type CompInput, type Computation, type Decided, type DimComputation, type SubComputation,
  type Weights } from "@/lib/api";

/** The run's computation (SCORE_AUDIT), refetched when the weights on the page change. Read-only: the API
 * rebuilds it from the saved data and never calls a model. */
export function useComputation(runId: string | null | undefined, weights: Weights | null | undefined, enabled: boolean) {
  const [c, setC] = useState<Computation | null>(null);
  const first = useRef(true);
  const key = JSON.stringify(weights ?? null);
  useEffect(() => {
    if (!enabled || !runId) return;
    const load = () => (weights ? post<Computation>(`/runs/${runId}/computation`, { weights })
      : get<Computation>(`/runs/${runId}/computation`)).then(setC).catch(() => setC(null));
    if (first.current) { first.current = false; load(); return; }
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, key, enabled]);
  return c;
}

// Up to 3 decimals, trailing zeros dropped (1, 0.5, 0.667): the same numbers the arithmetic lines use.
const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : String(Number(v.toFixed(3)));

function factorText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  return String(v);
}

function Who({ d }: { d: Decided }) {
  return (
    <li className="break-words">
      {d.question && <span className="text-muted-foreground">{d.question.replaceAll("_", " ")}{d.route ? ` (${d.route})` : ""}: </span>}
      <span className="font-medium">{d.label ?? "—"}</span>
      {" · "}{d.decided_by}
      {d.confidence !== null && d.confidence !== undefined && <> · confidence {d.confidence.toFixed(2)}</>}
      {d.fallback && <span className="text-amber-800"> · fallback from Jev (Jev&apos;s answer {d.fallback.label ?? "—"}
        {d.fallback.confidence !== null ? `, confidence ${d.fallback.confidence.toFixed(2)}` : ""}, not used)</span>}
      {d.reason && <span className="text-muted-foreground"> · {d.reason}</span>}
    </li>
  );
}

function InputsTable({ rows, excluded }: { rows: CompInput[]; excluded?: boolean }) {
  const factorKeys = Array.from(new Set(rows.flatMap((r) => Object.keys(r.factors ?? {}))));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse text-xs">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="py-1 pr-2 font-medium">Item</th>
            <th className="py-1 pr-2 font-medium">Label</th>
            <th className="py-1 pr-2 text-right font-medium">Value</th>
            <th className="py-1 pr-2 text-right font-medium">Relevance</th>
            {factorKeys.length > 0 && <th className="py-1 pr-2 font-medium">Other factors</th>}
            <th className="py-1 font-medium">{excluded ? "Why left out · decided by" : "Decided by"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b align-top last:border-b-0">
              <td className="max-w-[26rem] py-1.5 pr-2">
                <span className="mr-1 rounded bg-muted px-1 text-[10px] uppercase tracking-wide text-muted-foreground">{r.kind}</span>
                {r.source && <span className="mr-1 text-[10px] text-muted-foreground">{r.source}</span>}
                <span className="block whitespace-pre-wrap break-words">{r.text}</span>
              </td>
              <td className="py-1.5 pr-2 break-words">{r.label ?? "—"}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.value)}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.relevance)}</td>
              {factorKeys.length > 0 && (
                <td className="py-1.5 pr-2">
                  <dl className="space-y-0.5">{factorKeys.filter((k) => r.factors?.[k] !== undefined).map((k) => (
                    <div key={k} className="break-words"><dt className="inline text-muted-foreground">{k}: </dt>
                      <dd className="inline">{factorText(r.factors[k])}</dd></div>))}</dl>
                </td>
              )}
              <td className="py-1.5">
                {r.reason && <p className="mb-0.5 font-medium text-amber-900">{r.reason}</p>}
                <ul className="space-y-0.5">{r.decided_by.map((d, j) => <Who key={j} d={d} />)}</ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Narrow places (the recruiter's EEAT tab): one block per item instead of a table. */
function InputsStacked({ rows }: { rows: CompInput[] }) {
  return (
    <ul className="divide-y rounded-md border bg-white">
      {rows.map((r, i) => (
        <li key={i} className="space-y-1 px-2.5 py-2">
          <p><span className="mr-1 rounded bg-muted px-1 text-[10px] uppercase tracking-wide text-muted-foreground">{r.kind}</span>
            {r.source && <span className="text-[10px] text-muted-foreground">{r.source}</span>}</p>
          <p className="whitespace-pre-wrap break-words">{r.text}</p>
          <p className="break-words">
            <span className="text-muted-foreground">label </span><span className="font-medium">{r.label ?? "—"}</span>
            <span className="text-muted-foreground"> · value </span><span className="tabular-nums">{num(r.value)}</span>
            {r.relevance !== null && <><span className="text-muted-foreground"> · relevance </span><span className="tabular-nums">{num(r.relevance)}</span></>}
            {Object.entries(r.factors ?? {}).map(([k, v]) => <span key={k}><span className="text-muted-foreground"> · {k} </span>{factorText(v)}</span>)}
          </p>
          {r.reason && <p className="font-medium text-amber-900">Left out: {r.reason}</p>}
          <ul className="space-y-0.5 text-muted-foreground">{r.decided_by.map((d, j) => <Who key={j} d={d} />)}</ul>
        </li>
      ))}
    </ul>
  );
}

function Arithmetic({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return (
    <div className="space-y-0.5 rounded-md border bg-white px-3 py-2 font-mono text-xs leading-relaxed">
      {lines.map((l, i) => <p key={i} className="break-words">{l}</p>)}
    </div>
  );
}

/** One subcategory: inputs, items left out and why, the arithmetic with the real numbers, and the status. */
export function SubcategoryComputation({ c, compact = false }: { c: SubComputation | undefined; compact?: boolean }) {
  if (!c) return null;
  return (
    <section aria-label="Computation" data-testid="computation" className={cn("space-y-3", compact && "text-xs")}>
      <h4 className="flex items-center gap-1.5 font-medium"><Calculator className="size-4" /> Computation</h4>
      {!c.matches_saved && (
        <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          The profile changed after this score was computed. The saved score is {num(c.score)} ({c.status}); computed
          from the profile as it is now it would be {num(c.recomputed.score)} ({c.recomputed.status}). Check again to
          update it.
        </p>
      )}
      <p className="text-xs"><span className="font-medium">Status: </span>{c.status_reason}</p>
      {c.inputs.length > 0 && <div className="space-y-1"><p className="text-xs font-medium">Inputs used ({c.inputs.length})</p>
        {compact ? <InputsStacked rows={c.inputs} /> : <InputsTable rows={c.inputs} />}</div>}
      {c.excluded.length > 0 && <div className="space-y-1"><p className="text-xs font-medium">Left out of the formula ({c.excluded.length})</p>
        {compact ? <InputsStacked rows={c.excluded} /> : <InputsTable rows={c.excluded} excluded />}</div>}
      <Arithmetic lines={c.arithmetic} />
    </section>
  );
}

/** A dimension: Σ(w·s)/Σw with the real subcategory weights and values. */
export function DimensionComputation({ d, label }: { d: DimComputation | undefined; label: string }) {
  if (!d) return null;
  return (
    <details className="group border-t px-4 py-2 text-xs" data-testid="dimension-computation">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium text-muted-foreground hover:text-foreground">
        <Calculator className="size-3.5" /> How {label} is computed
      </summary>
      <div className="mt-2 space-y-2">
        <p className="text-muted-foreground">Weighted average of the subcategories: Σ(weight × score) / Σ weight.
          Missing counts as 0; N/A and couldn&apos;t-check are left out.</p>
        <table className="w-full max-w-xl text-xs">
          <tbody>
            {d.terms.map((t) => (
              <tr key={t.subcategory} className="border-b last:border-b-0">
                <td className="py-0.5 pr-2">{t.label}{t.missing && <span className="text-muted-foreground"> (missing → 0)</span>}</td>
                <td className="py-0.5 pr-2 text-right tabular-nums">weight {num(t.weight)}</td>
                <td className="py-0.5 text-right tabular-nums">score {num(t.score)}</td>
              </tr>
            ))}
            {d.excluded.map((t) => (
              <tr key={t.subcategory} className="border-b text-muted-foreground last:border-b-0">
                <td className="py-0.5 pr-2">{t.label}</td>
                <td className="py-0.5 pr-2 text-right tabular-nums">weight {num(t.weight)}</td>
                <td className="py-0.5 text-right">left out: {t.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Arithmetic lines={[d.arithmetic]} />
      </div>
    </details>
  );
}

/** The final score: 100 · Σ(W·dimension) / ΣW. */
export function FinalComputation({ c }: { c: Computation | null }) {
  if (!c) return null;
  return (
    <details className="rounded-lg border bg-white px-4 py-2 text-sm" data-testid="final-computation">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium">
        <Calculator className="size-4" /> How the final score is computed
      </summary>
      <div className="mt-2 space-y-2 text-xs">
        <p className="text-muted-foreground">100 × Σ(dimension weight × dimension score) / Σ dimension weight, over the
          dimensions that have a score{c.final.excluded.length ? ` (left out: ${c.final.excluded.join(", ")})` : ""}.
          Scored on {c.today} with logic {c.logic_version}.</p>
        <Arithmetic lines={[c.final.arithmetic]} />
      </div>
    </details>
  );
}
