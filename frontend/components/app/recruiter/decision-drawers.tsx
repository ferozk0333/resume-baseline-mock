"use client";
import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Send } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { EmailEditor, SendNotice } from "@/components/app/recruiter/email-editor";
import { get, post, put, type Email, type Reason, type Stage } from "@/lib/api";

const SOURCE: Record<string, string> = { "must-have": "Must-have", gap: "Top gap", "missing evidence": "Missing evidence" };

/** "Not moving forward": reasons (code) → draft (model or template) → recruiter edits → Send / Save / Cancel. */
export function RejectDrawer({ open, onOpenChange, applicationId, name, onSent }: {
  open: boolean; onOpenChange: (o: boolean) => void; applicationId: string; name: string | null; onSent: () => void;
}) {
  const [reasons, setReasons] = useState<Reason[]>([]);
  const [note, setNote] = useState("");
  const [email, setEmail] = useState<Email | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const show = (e: Email, rs: Reason[]) => {
    setEmail(e); setSubject(e.subject); setBody(e.body); setReasons(rs);
  };
  const draft = useCallback(async (ids?: string[], why?: string) => {
    setBusy("draft");
    try {
      const out = await post<{ email: Email; reasons: Reason[] }>(`/applications/${applicationId}/not-moving-forward`,
        ids ? { reason_ids: ids, note: why || null } : {});
      show(out.email, out.reasons);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  }, [applicationId]);

  useEffect(() => {
    if (!open) return;
    // Reopen a saved draft if there is one; otherwise draft from the code's pre-selected reasons.
    get<Email[]>(`/emails?application_id=${applicationId}&kind=not_moving_forward&status=draft`).then((saved) => {
      if (saved.length) {
        show(saved[0], saved[0].reasons?.reasons ?? []);
        setNote(saved[0].reasons?.note ?? "");
      } else draft();
    }).catch((e) => toast.error(e.message));
  }, [open, applicationId, draft]);

  async function act(action: "send" | "save" | "cancel") {
    if (!email) return;
    setBusy(action);
    try {
      if (action !== "cancel") await put(`/emails/${email.id}`, { subject, body });
      if (action === "send") {
        const sent = await post<Email>(`/emails/${email.id}/send`);
        if (sent.status !== "sent") throw new Error(sent.error ?? "Not sent.");
        toast.success(`Sent to ${sent.provider === "outbox" ? "the outbox" : sent.actual_to}`);
        onSent();
      } else if (action === "cancel") {
        await post(`/emails/${email.id}/cancel`);
        setEmail(null);
      } else toast.success("Draft saved");
      onOpenChange(false);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  }

  const selected = reasons.filter((r) => r.selected).map((r) => r.id);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle>Not moving forward · {name ?? "applicant"}</SheetTitle>
          <SheetDescription>Pick the reasons; the draft uses only these. Nothing is sent until you click Send.</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          <section aria-label="Reasons" className="space-y-1.5">
            <h3 className="text-sm font-semibold">Reasons</h3>
            {reasons.length === 0 && busy && <p className="text-xs text-muted-foreground">Finding reasons…</p>}
            <ul className="space-y-1">
              {reasons.map((r) => (
                <li key={r.id}>
                  <label className="flex items-start gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted/60">
                    <input type="checkbox" className="mt-1" checked={r.selected}
                      onChange={() => setReasons(reasons.map((x) => x.id === r.id ? { ...x, selected: !x.selected } : x))} />
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{r.title}</span>
                      <Badge variant="outline" className="ml-1.5 h-4 px-1 text-[10px]">{SOURCE[r.source] ?? r.source}</Badge>
                      <span className="block text-xs text-muted-foreground">{r.detail} {r.advice}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            <Textarea aria-label="Note for the draft" placeholder="Optional note (job-related only)" value={note}
              onChange={(e) => setNote(e.target.value)} className="min-h-14 bg-white text-sm" />
            <Button size="sm" variant="outline" disabled={!selected.length || busy !== null}
              onClick={() => draft(selected, note)}>
              {busy === "draft" ? <Loader2 className="animate-spin" /> : <RefreshCw />} Redraft from selected reasons
            </Button>
          </section>
          <section aria-label="Draft email" className="space-y-2">
            <h3 className="text-sm font-semibold">Draft email</h3>
            {email ? <EmailEditor email={email} subject={subject} body={body} onSubject={setSubject} onBody={setBody} limit={150} />
              : <p className="text-xs text-muted-foreground">{busy ? "Drafting…" : "No draft."}</p>}
            <SendNotice />
          </section>
        </div>
        <SheetFooter className="shrink-0 flex-row justify-end border-t bg-popover">
          <Button variant="ghost" disabled={!email || busy !== null} onClick={() => act("cancel")}>Cancel</Button>
          <Button variant="outline" disabled={!email || busy !== null} onClick={() => act("save")}>Save draft</Button>
          <Button disabled={!email || busy !== null} onClick={() => act("send")}>
            {busy === "send" ? <Loader2 className="animate-spin" /> : <Send />} Send
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/** Stage email on demand (later stages): draft → edit → Send moves the candidate to that stage. */
export function StageEmailDrawer({ open, onOpenChange, applicationId, name, stages, onSent }: {
  open: boolean; onOpenChange: (o: boolean) => void; applicationId: string; name: string | null; stages: Stage[];
  onSent: () => void;
}) {
  const [stageId, setStageId] = useState("");
  const [email, setEmail] = useState<Email | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  async function draft() {
    if (!stageId) return;
    setBusy(true);
    try {
      const e = await post<Email>(`/applications/${applicationId}/stage-email`, { stage_id: stageId });
      setEmail(e); setSubject(e.subject); setBody(e.body);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  }
  async function send() {
    if (!email) return;
    setBusy(true);
    try {
      await put(`/emails/${email.id}`, { subject, body });
      const sent = await post<Email>(`/emails/${email.id}/send`);
      if (sent.status !== "sent") throw new Error(sent.error ?? "Not sent.");
      toast.success("Sent; candidate moved to the stage");
      setEmail(null); onSent(); onOpenChange(false);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle>Stage email · {name ?? "applicant"}</SheetTitle>
          <SheetDescription>Invite the candidate to a stage. Sending moves them there.</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          <div className="flex gap-2">
            <select aria-label="Stage" value={stageId} onChange={(e) => setStageId(e.target.value)}
              className="h-8 flex-1 rounded-lg border border-input bg-white px-2 text-sm">
              <option value="">Choose a stage…</option>
              {stages.filter((s) => !s.terminal).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <Button size="sm" variant="outline" disabled={!stageId || busy} onClick={draft}>Draft</Button>
          </div>
          {email && <EmailEditor email={email} subject={subject} body={body} onSubject={setSubject} onBody={setBody} limit={120} />}
          <SendNotice />
        </div>
        <SheetFooter className="shrink-0 flex-row justify-end border-t bg-popover">
          <Button variant="ghost" onClick={() => { if (email) post(`/emails/${email.id}/cancel`); setEmail(null); onOpenChange(false); }}>Cancel</Button>
          <Button disabled={!email || busy} onClick={send}><Send /> Send</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
