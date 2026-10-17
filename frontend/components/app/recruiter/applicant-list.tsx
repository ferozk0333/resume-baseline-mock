"use client";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { topShare, type ApplicantRow } from "@/lib/api";

const ROW = 104;          // fixed row height for virtual scrolling (lists of 100+ applicants)
const OVERSCAN = 4;

export const STATUS_LABEL: Record<string, string> = { new: "New", shortlisted: "Shortlisted", in_loop: "In loop",
  not_moving_forward: "Not moving forward" };

/** Virtualized ranked list. Render the window from the container's own scroll position and measured height;
 *  the parent remounts it (key) when the rows change, so the list always starts at the top row. */
export function ApplicantList({ rows, selected, onSelect, picked, onPick, roleTitle }: {
  rows: ApplicantRow[]; selected: string | null; onSelect: (id: string) => void;
  picked: string[]; onPick: (id: string) => void; roleTitle?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, height: 800 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setView({ top: el.scrollTop, height: el.clientHeight || 800 });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Keep the selected row in view (↑/↓ keys): scrolling fires onScroll, which updates the window.
  const index = rows.findIndex((r) => r.application_id === selected);
  useEffect(() => {
    const el = ref.current;
    if (!el || index < 0) return;
    if (index * ROW < el.scrollTop) el.scrollTop = index * ROW;
    else if ((index + 1) * ROW > el.scrollTop + el.clientHeight) el.scrollTop = (index + 1) * ROW - el.clientHeight;
  }, [index]);

  const top = Math.min(view.top, Math.max(0, rows.length * ROW - view.height));
  const first = Math.max(0, Math.floor(top / ROW) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((top + view.height) / ROW) + OVERSCAN);
  return (
    <div ref={ref} role="list" aria-label="Applicants" className="min-h-0 flex-1 overflow-y-auto"
      onScroll={(e) => setView({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight })}>
      <div style={{ height: rows.length * ROW, position: "relative" }}>
        {rows.slice(first, last).map((r, i) => {
          const at = first + i;
          return (
            <div key={r.application_id} role="listitem"
              style={{ position: "absolute", top: at * ROW, height: ROW, left: 0, right: 0 }}
              className={cn("flex gap-2.5 border-b px-3 py-2 text-sm",
                selected === r.application_id ? "bg-primary/5 shadow-[inset_3px_0_0_var(--color-primary)]" : "hover:bg-muted/60")}>
              <input type="checkbox" aria-label={`Select ${r.name ?? "applicant"}`} className="mt-1"
                checked={picked.includes(r.application_id)} onChange={() => onPick(r.application_id)} />
              <button type="button" onClick={() => onSelect(r.application_id)} aria-current={selected === r.application_id}
                aria-label={`${r.name ?? "Applicant"}: match ${r.match === null ? "not scored" : Math.round(r.match)}`}
                className="flex min-w-0 flex-1 gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate font-medium">{r.name ?? "Unnamed"}</span>
                    {r.status !== "new" && <Badge variant={r.status === "not_moving_forward" ? "outline" : "default"}
                      className="h-4 shrink-0 px-1.5 text-[10px]">{r.stage ?? STATUS_LABEL[r.status]}</Badge>}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{r.current_role ?? "—"}</div>
                  <div className="mt-0.5 line-clamp-1 text-xs">{r.why ?? (r.scored ? "" : "Not scored yet")}</div>
                  <div className="mt-1 flex gap-1 overflow-hidden">
                    {r.tags.slice(0, 2).map((t) => <Badge key={t} variant="secondary" className="shrink-0 text-[10px]">{t}</Badge>)}
                    {r.demo && <Badge variant="outline" className="shrink-0 text-[10px] text-muted-foreground">
                      {r.demo === "Test archetype" ? "test archetype" : "demo"}</Badge>}
                  </div>
                </div>
                <div className="w-16 shrink-0 text-right">
                  <div className="text-lg leading-6 font-semibold tabular-nums text-primary">{r.match === null ? "—" : Math.round(r.match)}</div>
                  <div className="text-[11px] text-muted-foreground" title="Evidence confidence">
                    conf. {r.confidence === null ? "—" : Math.round(r.confidence)}
                  </div>
                  <div className="text-[11px] text-muted-foreground">must {r.scored ? `${r.must_haves_met}/${r.must_haves_total}` : "—"}</div>
                  {r.percentile !== null && r.percentile !== undefined && (
                    <div className="text-[11px] font-medium text-emerald-700"
                      title={`Higher than ${r.percentile}% of ${r.percentile_n ?? "the"} other candidates for this role${roleTitle ? ` (${roleTitle})` : ""}`}>
                      Top {topShare(r.percentile)}%
                    </div>
                  )}
                </div>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
