"use client";
import { AlertTriangle, Loader2, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ProfileResponse } from "@/lib/api";
import { useReviewApi } from "./review-api";

/** "Needs your attention" (specs/19 item 12): year-only dates, items the quote check dropped, dates the resume and
 * LinkedIn disagree on, and sources still being read. Each links to its card. Hidden when empty. */
export function AttentionBox({ data }: { data: ProfileResponse }) {
  const { apply } = useReviewApi();
  const items = [...data.profile.roles.map((r) => ({ ...r, kind: "role" as const, name: r.employer ? `${r.title} — ${r.employer}` : r.title })),
    ...data.profile.projects.map((p) => ({ ...p, kind: "project" as const, name: p.name }))];
  const years = items.filter((x) => x.year_only);
  const conflicts = items.filter((x) => x.conflict);
  const pending = data.pending ?? [];
  const dropped = data.dropped;
  if (!years.length && !conflicts.length && !pending.length && !dropped.length) return null;

  const jump = (id: string) => {
    const el = document.getElementById(`card-${id}`);
    el?.scrollIntoView({ block: "center" });
    el?.focus({ preventScroll: true });
  };
  const Link = ({ id, children }: { id: string; children: React.ReactNode }) => (
    <a href={`#card-${id}`} onClick={(e) => { e.preventDefault(); jump(id); }} className="font-medium text-primary underline-offset-2 hover:underline">{children}</a>
  );

  return (
    <section aria-labelledby="attention-title" data-testid="attention-box"
      className="rounded-xl border border-amber-300 bg-amber-50/70 px-4 py-3">
      <h2 id="attention-title" className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-950">
        <AlertTriangle className="size-4 text-amber-700" aria-hidden /> Needs your attention
      </h2>
      <ul className="space-y-1.5 text-sm">
        {pending.map((p) => (
          <li key={p.type} className="flex items-center gap-2" role="status" data-testid="still-reading">
            <Loader2 className="size-3.5 text-primary motion-safe:animate-spin" aria-hidden />
            <span>Still reading {p.label}… <span className="text-muted-foreground">New items appear as it finishes.</span></span>
          </li>
        ))}
        {years.map((x) => (
          <li key={`y-${x.id}`}><Link id={x.id}>{x.name}</Link>: we only found the year. Please check the months.</li>
        ))}
        {conflicts.map((x) => (
          <li key={`c-${x.id}`}><Link id={x.id}>{x.name}</Link>: {x.conflict}</li>
        ))}
        {dropped.map((d) => (
          <li key={`d-${d.id}`} className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 flex-1">Left out because the exact text wasn&apos;t found in your document: <span className="font-medium">{d.text}</span></span>
            <Button type="button" size="xs" variant="outline" onClick={() => apply({ op: "add_back", item: "dropped", id: d.id }, "Added back")}>
              <Undo2 /> Add back
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
