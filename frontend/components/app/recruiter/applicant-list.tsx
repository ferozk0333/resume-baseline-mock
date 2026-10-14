"use client";
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ApplicantRow } from "@/lib/api";

const ROW = 112;          // fixed row height for virtual scrolling (lists of 100+ applicants)
const OVERSCAN = 4;

export const STATUS_LABEL: Record<string, string> = { new: "New", shortlisted: "Shortlisted",
  not_moving_forward: "Not moving forward" };

export function ApplicantList({ rows, selected, onSelect, picked, onPick }: {
  rows: ApplicantRow[]; selected: string | null; onSelect: (id: string) => void;
  picked: string[]; onPick: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const height = 640;
  const first = Math.max(0, Math.floor(top / ROW) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((top + height) / ROW) + OVERSCAN);
  return (
    <div ref={ref} role="list" aria-label="Applicants" className="h-[calc(100vh-15rem)] min-h-96 overflow-y-auto"
      onScroll={(e) => setTop(e.currentTarget.scrollTop)}>
      <div style={{ height: rows.length * ROW, position: "relative" }}>
        {rows.slice(first, last).map((r, i) => {
          const index = first + i;
          return (
            <div key={r.application_id} role="listitem"
              style={{ position: "absolute", top: index * ROW, height: ROW, left: 0, right: 0 }}
              className={cn("flex gap-3 border-b px-3 py-2.5 text-sm",
                selected === r.application_id ? "bg-primary/5" : "hover:bg-muted/60")}>
              <input type="checkbox" aria-label={`Compare ${r.name ?? "applicant"}`} className="mt-1"
                checked={picked.includes(r.application_id)} onChange={() => onPick(r.application_id)} />
              <button type="button" onClick={() => onSelect(r.application_id)} aria-current={selected === r.application_id}
                aria-label={`${r.name ?? "Applicant"}: match ${r.match === null ? "not scored" : Math.round(r.match)}`}
                className="flex min-w-0 flex-1 gap-3 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{r.name ?? "Unnamed"}</span>
                    {r.status !== "new" && <Badge variant={r.status === "shortlisted" ? "default" : "outline"}
                      className="shrink-0">{STATUS_LABEL[r.status]}</Badge>}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{r.current_role ?? "—"}</div>
                  <div className="mt-1 line-clamp-1 text-xs">{r.why ?? (r.scored ? "" : "Not scored yet")}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {r.tags.map((t) => <Badge key={t} variant="secondary" className="text-[11px]">{t}</Badge>)}
                    {r.demo && <Badge variant="outline" className="text-[11px] text-muted-foreground">demo</Badge>}
                  </div>
                </div>
                <div className="w-20 shrink-0 text-right">
                  <div className="text-lg font-semibold tabular-nums text-primary">{r.match === null ? "—" : Math.round(r.match)}</div>
                  <div className="text-[11px] text-muted-foreground" title="Evidence confidence">
                    conf. {r.confidence === null ? "—" : Math.round(r.confidence)}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    must {r.scored ? `${r.must_haves_met}/${r.must_haves_total}` : "—"}
                  </div>
                  {r.percentile !== null && r.percentile !== undefined && (
                    <div className="text-[11px] text-muted-foreground" title="Percentile within this job's applicants">
                      p{r.percentile}
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
