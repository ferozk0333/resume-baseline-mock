"use client";
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Judgment, SubComputation, Subcategory } from "@/lib/api";
import { SubcategoryComputation } from "@/components/features/score/computation";

const STATUS: Record<string, { label: string; className: string }> = {
  scored: { label: "shown", className: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  missing: { label: "missing", className: "bg-amber-50 text-amber-800 border-amber-200" },
  na: { label: "N/A", className: "bg-slate-100 text-slate-600 border-slate-200" },
  error: { label: "couldn't check", className: "bg-red-50 text-red-700 border-red-200" },
  pending: { label: "checking…", className: "bg-sky-50 text-sky-700 border-sky-200" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.error;
  return <Badge variant="outline" className={s.className}>{s.label}</Badge>;
}

// Breakdown values for people: internal ids hidden, objects as "key value" pairs.
function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.map(show).join("; ") : "none";
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .filter(([k]) => !/(^|_)ids?$/.test(k))
      .map(([k, x]) => `${k.replaceAll("_", " ")} ${show(x)}`).join(" · ");
  }
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

/** One area of the score. Candidates see the rule, the guidance and their evidence; `technical` (Admin → Demo
 * tools) adds the raw breakdown and how each piece of evidence was labelled. */
export function SubcategoryRow({ sub, technical = false, computation }: { sub: Subcategory; technical?: boolean;
  computation?: SubComputation }) {
  const [open, setOpen] = useState(false);
  const judgments = (sub.details.judgments ?? []) as Judgment[];
  const breakdown = Object.entries(sub.details).filter(([k]) => k !== "judgments" && !/(^|_)ids?$/.test(k));
  return (
    <div className="border-t first:border-t-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50">
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90")} />
        <span className="flex-1 text-sm font-medium">{sub.label}</span>
        <StatusBadge status={sub.status} />
        <span className="w-12 text-right text-sm tabular-nums">{sub.score === null ? "—" : sub.score.toFixed(2)}</span>
        <span className="w-16 text-right text-xs tabular-nums text-muted-foreground"
          title="Share of the final score after N/A and errors are excluded">
          {(sub.effective_weight * 100).toFixed(1)}%
        </span>
      </button>
      {sub.status === "missing" && sub.guidance && (
        <p className="mx-4 mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">{sub.guidance}</p>
      )}
      {open && (
        <div className="space-y-4 bg-muted/30 px-11 py-4 text-sm">
          {sub.formula && <p><span className="font-medium">How it is scored: </span>{sub.formula}</p>}
          {technical && breakdown.length > 0 && (
            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
              {breakdown.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k.replaceAll("_", " ")}</dt>
                  <dd className="break-words">{show(v)}</dd>
                </div>
              ))}
            </dl>
          )}
          {judgments.length > 0 && (
            <div className="space-y-2">
              <h4 className="font-medium">Evidence</h4>
              <ul className="space-y-2">
                {judgments.map((j, i) => (
                  <li key={i} className="rounded-md border bg-white px-3 py-2">
                    {technical && (
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span>{j.question.replaceAll("_", " ")}</span>
                        <Badge variant="secondary">{j.label ?? "no label"}</Badge>
                        <span>{j.method}{j.confidence !== null && j.confidence !== undefined ? ` · confidence ${j.confidence.toFixed(2)}` : ""}</span>
                      </div>
                    )}
                    {j.quote ? <blockquote className="mt-1 border-l-2 pl-2">“{j.quote}”</blockquote> : (
                      <p className="mt-1 text-muted-foreground">
                        {j.subject && <span className="text-foreground">{j.subject}</span>}
                        {technical && j.method === "jev" && <span className="ml-1 italic">(Jev returns no quote)</span>}
                      </p>
                    )}
                    {technical && j.reasoning && j.method !== "rule" && <p className="mt-1 text-xs text-muted-foreground">{j.reasoning}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <SubcategoryComputation c={computation} />
        </div>
      )}
    </div>
  );
}
