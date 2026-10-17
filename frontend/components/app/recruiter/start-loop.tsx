"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError, post } from "@/lib/api";

/** "Start interview loop" for one candidate or several, from any status (specs/16 §0). Candidates who are not
 *  Shortlisted are moved there after a confirmation. One candidate opens their workflow; several open Tasks. */
export function useStartLoop(jobId: string, onDone?: () => void) {
  const router = useRouter();
  const [ask, setAsk] = useState<{ ids: string[]; message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function start(ids: string[], confirm = false) {
    if (!ids.length) return;
    setBusy(true);
    try {
      if (!confirm) {                    // ask first when someone would move to Shortlisted
        const { needs_move } = await post<{ needs_move: string[] }>(`/jobs/${jobId}/workflows`,
          { application_ids: ids, check_only: true });
        if (needs_move.length) {
          setAsk({ ids, message: needs_move.length === 1 && ids.length === 1 ? "" : `(${needs_move.join(", ")})` });
          return;
        }
      }
      if (ids.length === 1) await post(`/applications/${ids[0]}/workflow`, { confirm_move: confirm });
      else await post(`/jobs/${jobId}/workflows`, { application_ids: ids, confirm_move: confirm });
      setAsk(null);
      onDone?.();
      router.push(ids.length === 1 ? `/recruiter/applications/${ids[0]}/workflow` : "/recruiter/tasks");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setAsk({ ids, message: "" });
      else toast.error((e as Error).message);
    } finally { setBusy(false); }
  }

  const dialog = (
    <Dialog open={!!ask} onOpenChange={(o) => !o && setAsk(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Move to Shortlisted and start?</DialogTitle>
          <DialogDescription>
            {ask?.message ? `Not shortlisted yet: ${ask.message.slice(1, -1)}.` : "This candidate is not shortlisted yet."}
            {" "}The interview loop starts from Shortlisted. Nothing is sent until you approve each email.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setAsk(null)}>Cancel</Button>
          <Button disabled={busy} onClick={() => ask && start(ask.ids, true)}>Move and start</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
  return { start, busy, dialog };
}
