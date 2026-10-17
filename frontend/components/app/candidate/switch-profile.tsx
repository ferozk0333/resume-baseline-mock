"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { get, type Candidate } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";
import { cn } from "@/lib/utils";

/** Header line (specs/17 §2): "Demo: viewing as <name> · Switch profile". No sign-in in the demo. */
export function ViewingAs() {
  const router = useRouter();
  const [current, ready, setCurrent] = useCurrentCandidate();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<Candidate[] | null>(null);
  useEffect(() => {
    if (ready) get<Candidate[]>("/candidates").then(setList).catch(() => setList([]));
  }, [ready, current, open]);
  if (!ready) return null;
  const me = list?.find((c) => c.id === current);

  return (
    <>
      <p className="text-xs whitespace-nowrap text-muted-foreground">
        {/* Short on laptop widths so the header stays on one row; the full sentence from 1280 px. */}
        <span className="hidden xl:inline">Demo: </span>
        {me ? <><span className="hidden xl:inline">viewing as </span>
          <span className="inline-block max-w-[10rem] truncate align-bottom font-medium text-foreground xl:max-w-[16rem]"
            title={me.name || "Unnamed"}>{me.name || "Unnamed"}</span></> : "no profile yet"}
        {" · "}
        <button type="button" aria-label="Switch profile" className="font-medium text-primary hover:underline" onClick={() => setOpen(true)}>
          Switch<span className="hidden xl:inline"> profile</span></button>
      </p>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Switch profile</DialogTitle>
            <DialogDescription>The demo has no sign-in. Pick whose candidate view to see.</DialogDescription>
          </DialogHeader>
          <ul aria-label="Saved profiles" className="max-h-72 space-y-1 overflow-y-auto">
            {(list ?? []).map((c) => (
              <li key={c.id}>
                <button type="button" aria-current={c.id === current ? "true" : undefined}
                  onClick={() => { setCurrent(c.id); setOpen(false); router.push("/"); }}
                  className={cn("flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-muted",
                    c.id === current && "bg-primary/10")}>
                  <span className="font-medium">{c.name || "Unnamed"}</span>
                  <span className="truncate text-xs text-muted-foreground">{c.target_role}</span>
                </button>
              </li>
            ))}
            {list?.length === 0 && <li className="px-3 py-2 text-sm text-muted-foreground">No saved profiles yet.</li>}
          </ul>
          <Button variant="outline" onClick={() => { setCurrent(null); setOpen(false); router.push("/onboarding"); }}>
            <Plus /> New profile
          </Button>
        </DialogContent>
      </Dialog>
    </>
  );
}
