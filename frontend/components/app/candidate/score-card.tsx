"use client";
import { Info, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DIMENSIONS, titleCase, type ScoreCard } from "@/lib/api";

/** "EEAT", said once per screen, with what it stands for. */
export function EeatTip() {
  return (
    <Tooltip>
      <TooltipTrigger render={<button type="button" className="inline-flex items-center gap-0.5 font-medium underline decoration-dotted underline-offset-2" />}>
        EEAT <Info className="size-3.5" aria-hidden />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        Experience (relevant work over time), Expertise (skills and tools you show in real work), Authority (outcomes,
        ownership and reach), Trust (how well your proof is backed: links, references, consistency).
      </TooltipContent>
    </Tooltip>
  );
}

export function PercentileLine({ card }: { card: ScoreCard }) {
  const p = card.percentile;
  if (p?.shown && p.top !== null) {
    return <p className="text-sm"><span className="font-medium">Top {p.top}%</span>
      <span className="text-muted-foreground"> of candidates scored for {card.title}</span></p>;
  }
  return (
    <p className="text-sm text-muted-foreground">
      Percentile not available yet: fewer than {p?.min_pool ?? 20} other candidates have been scored for {card.title}.
    </p>
  );
}

/** One role's baseline score (specs/17 §3.1): score, percentile or its empty state, 4 dimensions, top 3 actions. */
export function RoleScoreCard({ card, onOpenPlan, children }: { card: ScoreCard; onOpenPlan?: () => void;
  children?: React.ReactNode }) {
  const pending = card.status !== "scored";
  return (
    <Card data-testid={`score-card-${card.baseline_id}`}>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 space-y-0">
        <CardTitle className="text-base">{card.title}</CardTitle>
        {card.is_primary && <Badge>Primary</Badge>}
        {card.baseline_source === "market" && <Badge variant="outline">Market baseline</Badge>}
      </CardHeader>
      <CardContent className="space-y-5">
        {pending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            {card.status === "running" ? <><Loader2 className="size-4 animate-spin" /> Scoring…</> : "Not scored yet."}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
              <div>
                <div className="text-xs text-muted-foreground">Baseline score</div>
                <div className="text-4xl font-semibold tabular-nums text-primary">{card.final?.toFixed(0) ?? "—"}
                  <span className="ml-1 text-sm font-normal text-muted-foreground">/ 100</span></div>
              </div>
              <div className="min-w-0 flex-1 basis-48"><PercentileLine card={card} /></div>
            </div>
            {card.thin && (
              <p className="rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-900">
                Most of your score is missing evidence, not weak evidence. The actions below add the most.
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {DIMENSIONS.map((d) => {
                const v = card.dimensions[d];
                const pct = v === null || v === undefined ? null : Math.round(v * 100);
                return (
                  <div key={d}>
                    <div className="flex justify-between text-sm"><span>{titleCase(d)}</span>
                      <span className="tabular-nums text-muted-foreground">{pct ?? "Not applicable"}</span></div>
                    <div className="mt-1 h-2 rounded-full bg-muted" role="meter" aria-label={titleCase(d)}
                      aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? 0}>
                      <div className="h-full rounded-full bg-primary" style={{ width: `${pct ?? 0}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-medium">Top actions</h3>
              {card.actions.length > 0 ? (
                <ol className="space-y-2">
                  {card.actions.map((a, i) => (
                    <li key={i} className="flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm">
                      <span>{a.title}</span>
                      <span className="shrink-0 font-medium text-primary">up to {a.points.toFixed(1)} points</span>
                    </li>
                  ))}
                </ol>
              ) : card.plan_status === "generating" ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Preparing your plan…</p>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">
                    {card.plan_status === "failed" ? "Your plan could not be prepared. Your biggest gaps so far:"
                      : "Your plan for this role is prepared when you open it. Your biggest gaps so far:"}
                  </p>
                  <ol className="space-y-2">
                    {card.gaps.map((g, i) => (
                      <li key={i} className="flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm">
                        <span>{g.label} <span className="text-muted-foreground">({titleCase(g.dimension)})</span></span>
                        <span className="shrink-0 font-medium text-primary">{g.gain_text}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
            {onOpenPlan && <Button variant="outline" size="sm" onClick={onOpenPlan}>
              {card.plan_status === "ready" ? "See the plan" : "Open the plan"}</Button>}
          </>
        )}
        {children}
      </CardContent>
    </Card>
  );
}
