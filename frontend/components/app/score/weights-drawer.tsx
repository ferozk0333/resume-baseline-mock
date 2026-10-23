"use client";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Slider } from "@/components/ui/slider";
import { DIMENSIONS, get, titleCase, type Weights } from "@/lib/api";

const pct = (part: number, all: number) => (all > 0 ? Math.round((100 * part) / all) : 0);

export function WeightsDrawer({ open, onOpenChange, weights, labels, onChange }: {
  open: boolean; onOpenChange: (o: boolean) => void; weights: Weights; labels: Record<string, string>;
  onChange: (w: Weights) => void;
}) {
  const w = weights;
  const update = onChange;
  const dimTotal = DIMENSIONS.reduce((s, d) => s + (w.dimensions[d] ?? 0), 0);

  async function reset() {
    const d = await get<{ weights: Weights }>("/config/weights");
    update(d.weights);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Weights</SheetTitle>
          <SheetDescription>Change how much each part counts.</SheetDescription>
        </SheetHeader>
        <div className="space-y-6 px-4 pb-6">
          <section className="space-y-4">
            <h3 className="text-sm font-medium">Dimensions</h3>
            {DIMENSIONS.map((d) => (
              <WeightRow key={d} label={titleCase(d)} value={w.dimensions[d]} share={pct(w.dimensions[d], dimTotal)}
                onValue={(v) => update({ ...w, dimensions: { ...w.dimensions, [d]: v } })} />
            ))}
          </section>
          {DIMENSIONS.map((d) => {
            const subs = w.subcategories[d];
            const total = Object.values(subs).reduce((a, b) => a + b, 0);
            return (
              <details key={d} className="rounded-lg border">
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">{titleCase(d)} subcategories</summary>
                <div className="space-y-4 border-t px-3 py-3">
                  {Object.entries(subs).map(([s, v]) => (
                    <WeightRow key={s} label={labels[s] ?? s} value={v} share={pct(v, total)}
                      onValue={(x) => update({ ...w, subcategories: { ...w.subcategories, [d]: { ...subs, [s]: x } } })} />
                  ))}
                </div>
              </details>
            );
          })}
          <Button variant="outline" onClick={reset}>Reset to table</Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function WeightRow({ label, value, share, onValue }: {
  label: string; value: number; share: number; onValue: (v: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span id={`w-${label}`}>{label}</span>
        <span className="tabular-nums text-muted-foreground">{Math.round(value)} · {share}%</span>
      </div>
      <Slider aria-labelledby={`w-${label}`} min={0} max={100} step={1} value={[Math.round(value)]}
        onValueChange={(v) => onValue(Array.isArray(v) ? v[0] : v)} />
    </div>
  );
}
