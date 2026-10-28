"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError, get, post, type RunBaselines, type Task } from "@/lib/api";
import { cn } from "@/lib/utils";

/** "Scored against: Standard baseline | Market baseline (vN, date)" (specs/15 review). Standard is the default;
 *  Market appears only when a version is published for the role. Switching re-scores after a notice. */
export function BaselineSwitch({ runId }: { runId: string }) {
  const router = useRouter();
  const [opts, setOpts] = useState<RunBaselines | null>(null);
  const [notice, setNotice] = useState<{ source: "standard" | "market"; target: string; calls: number; reused: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { get<RunBaselines>(`/runs/${runId}/baselines`).then(setOpts).catch(() => setOpts(null)); }, [runId]);
  if (!opts?.role || !opts.standard) return null;

  async function choose(source: "standard" | "market", confirm = false) {
    setBusy(true);
    try {
      const { task_id } = await post<{ task_id: string }>(`/runs/${runId}/switch-baseline`, { source, confirm });
      setNotice(null);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        const t = await get<Task>(`/tasks/${task_id}`);
        if (t.status === "error") throw new Error(t.error ?? "Scoring failed.");
        if (t.run_id) { router.push(`/runs/${t.run_id}`); return; }
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNotice({ source, ...JSON.parse(e.message) });
      else toast.error((e as Error).message);
      setBusy(false);
    }
  }
  const items = [{ key: "standard" as const, label: opts.standard.label },
    ...(opts.market ? [{ key: "market" as const, label: opts.market.label }] : [])];
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Scored against:</span>
      <div role="radiogroup" aria-label="Scored against" className="flex rounded-lg border bg-muted p-0.5">
        {items.map((it) => {
          const on = opts.current === it.key && (it.key === "standard" || opts.current_version === opts.market?.version);
          return (
            <button key={it.key} type="button" role="radio" aria-checked={on} disabled={busy}
              onClick={() => !on && choose(it.key)}
              className={cn("rounded-md px-3 py-1", on ? "bg-white shadow-sm" : "text-muted-foreground hover:text-foreground")}>
              {it.label}
            </button>
          );
        })}
      </div>
      {opts.market && <span className="text-xs text-muted-foreground" data-testid="market-basis">Market: {opts.market.basis}</span>}
      {opts.current === "market" && opts.current_version !== opts.market?.version && (
        <span className="text-xs text-muted-foreground">This score used Market v{opts.current_version}.</span>
      )}
      {busy && !notice && <span className="flex items-center gap-1 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" /> Scoring…</span>}
      <Dialog open={!!notice} onOpenChange={(o) => { if (!o) { setNotice(null); setBusy(false); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Re-score against {notice?.target}?</DialogTitle>
            <DialogDescription>
              Your profile is scored again against {notice?.target}.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setNotice(null); setBusy(false); }}>Cancel</Button>
            <Button onClick={() => notice && choose(notice.source, true)}>Re-score</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
