"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

type Ask = { title: string; body: string; confirm: string; cancel: string; destructive?: boolean };

type Ctx = {
  /** The card in edit mode (only one at a time), or null. */
  editing: string | null;
  dirty: boolean;
  setDirty: (d: boolean) => void;
  /** Open a card for editing. Another card with unsaved changes asks first; resolves false if the person stays. */
  startEdit: (id: string) => Promise<boolean>;
  /** Close the card (after Save, or Cancel). Cancel with changes asks first. */
  stopEdit: (id: string, opts?: { discard?: boolean }) => Promise<boolean>;
  confirm: (a: Ask) => Promise<boolean>;
};

const EditCtx = createContext<Ctx | null>(null);

export function useReviewEdit(): Ctx {
  const c = useContext(EditCtx);
  if (!c) throw new Error("useReviewEdit needs <ReviewEditProvider>");
  return c;
}

const DISCARD: Ask = { title: "Discard your changes?", body: "You have changes on this card that are not saved.",
  confirm: "Discard changes", cancel: "Keep editing", destructive: true };

/** One card in edit mode at a time (specs/19 item 7), the unsaved-changes prompt, and a promise-based confirm. */
export function ReviewEditProvider({ children }: { children: React.ReactNode }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [ask, setAsk] = useState<Ask | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback((a: Ask) => new Promise<boolean>((resolve) => {
    resolver.current = resolve;
    setAsk(a);
  }), []);
  const answer = (ok: boolean) => { resolver.current?.(ok); resolver.current = null; setAsk(null); };

  const startEdit = useCallback(async (id: string) => {
    if (editing && editing !== id && dirty && !(await confirm(DISCARD))) return false;
    setDirty(false);
    setEditing(id);
    return true;
  }, [editing, dirty, confirm]);

  const stopEdit = useCallback(async (id: string, opts?: { discard?: boolean }) => {
    if (opts?.discard && dirty && !(await confirm(DISCARD))) return false;
    setEditing((cur) => (cur === id ? null : cur));
    setDirty(false);
    return true;
  }, [dirty, confirm]);

  useEffect(() => {                                 // leaving the page with unsaved changes
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const value = useMemo(() => ({ editing, dirty, setDirty, startEdit, stopEdit, confirm }),
    [editing, dirty, startEdit, stopEdit, confirm]);
  return (
    <EditCtx.Provider value={value}>
      {children}
      <Dialog open={!!ask} onOpenChange={(o) => { if (!o) answer(false); }}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{ask?.title}</DialogTitle>
            <DialogDescription>{ask?.body}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => answer(false)}>{ask?.cancel}</Button>
            <Button variant={ask?.destructive ? "destructive" : "default"} onClick={() => answer(true)}>{ask?.confirm}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </EditCtx.Provider>
  );
}
