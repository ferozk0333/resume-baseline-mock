"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Check, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmailEditor, SendNotice } from "@/components/app/recruiter/email-editor";
import { get, post, put, type Email } from "@/lib/api";

export default function LoopPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><LoopReview /></Suspense>;
}

/** All scheduling drafts of one "Start interview loop" in one screen: edit any, send one by one or all. */
function LoopReview() {
  const { id, batch } = useParams<{ id: string; batch: string }>();
  const [drafts, setDrafts] = useState<Email[] | null>(null);
  const [edits, setEdits] = useState<Record<string, { subject: string; body: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    get<{ drafts: Email[] }>(`/jobs/${id}/interview-loop/${batch}`).then((d) => {
      setDrafts(d.drafts);
      setEdits((e) => Object.fromEntries(d.drafts.map((x) => [x.id, e[x.id] ?? { subject: x.subject, body: x.body }])));
    }).catch((e) => toast.error(e.message));
  }, [id, batch]);
  useEffect(() => { load(); }, [load]);

  async function send(ids: string[]) {
    setBusy(ids.length > 1 ? "all" : ids[0]);
    try {
      for (const eid of ids) {
        await put(`/emails/${eid}`, edits[eid]);
        const sent = await post<Email>(`/emails/${eid}/send`);
        if (sent.status !== "sent") throw new Error(`${sent.candidate_name}: ${sent.error ?? "not sent"}`);
      }
      toast.success(`${ids.length} email${ids.length === 1 ? "" : "s"} sent; moved to Scheduling`);
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); load(); }
  }
  async function remove(eid: string) {
    await post(`/emails/${eid}/cancel`);
    load();
  }

  if (!drafts) return <Skeleton className="h-96" />;
  const pending = drafts.filter((d) => d.status === "draft").map((d) => d.id);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href={`/recruiter/jobs/${id}?view=pipeline`} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" /> Back to the job
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Review scheduling emails</h1>
          <p className="text-sm text-muted-foreground">Edit any draft, then send them one by one or all at once. Each candidate moves to Scheduling when theirs is sent.</p>
        </div>
        <Button disabled={!pending.length || busy !== null} onClick={() => send(pending)}>
          {busy === "all" ? <Loader2 className="animate-spin" /> : <Send />} Approve all and send ({pending.length})
        </Button>
      </div>
      <SendNotice />
      <div className="grid gap-4 lg:grid-cols-2">
        {drafts.map((d) => (
          <Card key={d.id} className="gap-3 p-4">
            <div className="flex items-center gap-2">
              <h2 className="font-semibold">{d.candidate_name}</h2>
              <Badge variant={d.status === "sent" ? "default" : "outline"} className="capitalize">{d.status}</Badge>
              {d.checks.evidence_id && <span className="ml-auto text-xs text-muted-foreground">cites evidence {d.checks.evidence_id.slice(0, 8)}</span>}
            </div>
            {d.status === "draft" ? (
              <>
                <EmailEditor email={d} subject={edits[d.id]?.subject ?? d.subject} body={edits[d.id]?.body ?? d.body}
                  onSubject={(s) => setEdits({ ...edits, [d.id]: { ...edits[d.id], subject: s } })}
                  onBody={(b) => setEdits({ ...edits, [d.id]: { ...edits[d.id], body: b } })} limit={120} />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={() => remove(d.id)}>Remove</Button>
                  <Button size="sm" disabled={busy !== null} onClick={() => send([d.id])}>
                    {busy === d.id ? <Loader2 className="animate-spin" /> : <Send />} Send
                  </Button>
                </div>
              </>
            ) : (
              <div className="text-sm">
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  {d.status === "sent" && <Check className="size-3.5 text-emerald-600" />}
                  {d.status === "sent" ? `Sent to ${d.actual_to}` : d.status === "failed" ? d.error : "Removed from this batch"}
                </div>
                <div className="font-medium">{d.subject}</div>
                <pre className="mt-1 max-h-40 overflow-y-auto font-[inherit] text-xs whitespace-pre-wrap text-muted-foreground">{d.body}</pre>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
