"use client";
import { useEffect, useState } from "react";
import { Briefcase, ChevronLeft, ChevronRight, Compass, FileCheck, Gauge, Gem, Layers, Lightbulb, ListChecks, Repeat,
  Sparkles, Target, TrendingUp, UserCheck, type LucideIcon } from "lucide-react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { get, type WaitTip } from "@/lib/api";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = { repeat: Repeat, "file-check": FileCheck, layers: Layers, "user-check": UserCheck,
  gauge: Gauge, compass: Compass, sparkles: Sparkles, gem: Gem, briefcase: Briefcase, target: Target,
  "list-checks": ListChecks, "trending-up": TrendingUp };
export const TIP_SECONDS = 8;

/** "Good to know" (specs/17 wait screens): one tip at a time from config/wait_tips.yaml. Advances every 8 s; pauses
 * on hover or focus; stops for good once the visitor uses an arrow or a dot; never moves with reduced motion. */
export function GoodToKnow({ kind }: { kind: "reading" | "scoring" }) {
  const reduced = useReducedMotion();
  const [tips, setTips] = useState<WaitTip[]>([]);
  const [i, setI] = useState(0);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [manual, setManual] = useState(false);
  useEffect(() => {
    get<Record<"reading" | "scoring", WaitTip[]>>("/config/wait-tips").then((t) => setTips(t[kind] ?? [])).catch(() => setTips([]));
  }, [kind]);
  const auto = tips.length > 1 && !reduced && !hover && !focus && !manual;
  useEffect(() => {
    if (!auto) return;
    const t = setTimeout(() => setI((x) => (x + 1) % tips.length), TIP_SECONDS * 1000);
    return () => clearTimeout(t);
  }, [auto, i, tips.length]);

  if (tips.length === 0) return null;
  const tip = tips[i % tips.length];
  const Icon = (tip.icon && ICONS[tip.icon]) || Lightbulb;
  const go = (n: number) => { setManual(true); setI((n + tips.length) % tips.length); };

  return (
    <section aria-roledescription="carousel" aria-label="Good to know" data-testid={`tips-${kind}`}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onFocus={() => setFocus(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setFocus(false); }}
      onKeyDown={(e) => { if (e.key === "ArrowRight") go(i + 1); if (e.key === "ArrowLeft") go(i - 1); }}
      className="h-fit rounded-xl border bg-white p-4">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Good to know</div>
      <div key={i} aria-roledescription="slide" aria-label={`${i % tips.length + 1} of ${tips.length}`}
        aria-live={manual ? "polite" : "off"}
        className="flex min-h-24 gap-3 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-300">
        <Icon className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <div>
          <p className="font-medium" data-testid="tip-title">{tip.title}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{tip.text}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <div className="flex gap-1.5" role="group" aria-label="Choose a tip">
          {tips.map((t, n) => (
            <button key={t.title} type="button" aria-label={`Tip ${n + 1}: ${t.title}`} aria-current={n === i ? "true" : undefined}
              onClick={() => go(n)}
              className={cn("size-2 rounded-full transition-colors", n === i ? "bg-primary" : "bg-muted-foreground/30 hover:bg-muted-foreground/60")} />
          ))}
        </div>
        <div className="flex gap-1">
          <button type="button" aria-label="Previous tip" onClick={() => go(i - 1)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"><ChevronLeft className="size-4" /></button>
          <button type="button" aria-label="Next tip" onClick={() => go(i + 1)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"><ChevronRight className="size-4" /></button>
        </div>
      </div>
    </section>
  );
}
