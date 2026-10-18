"use client";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SendNotice } from "@/components/app/recruiter/email-editor";
import { get, type Email } from "@/lib/api";
import { cn } from "@/lib/utils";

const KIND: Record<string, string> = { not_moving_forward: "Not moving forward", scheduling: "Scheduling", stage: "Stage",
  nudge: "Nudge", answer: "Answer", confirmation: "Confirmation", offer: "Offer note" };
const FILTERS = [["sent", "Sent"], ["draft", "Drafts"], ["failed", "Failed"], ["", "All"]] as const;

/** Every email (specs/13 §4): intended and actual recipient, subject, body, model or template, status, time. */
export default function OutboxPage() {
  const [status, setStatus] = useState("sent");
  const [emails, setEmails] = useState<Email[] | null>(null);
  useEffect(() => { get<Email[]>(`/emails${status ? `?status=${status}` : ""}`).then(setEmails).catch(() => setEmails([])); }, [status]);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Outbox</h1>
      </div>
      <SendNotice />
      <div role="group" aria-label="Filter" className="flex w-fit rounded-lg border bg-muted p-0.5 text-sm">
        {FILTERS.map(([v, l]) => (
          <button key={v} type="button" aria-pressed={status === v} onClick={() => setStatus(v)}
            className={cn("rounded-md px-3 py-0.5", status === v ? "bg-white shadow-sm" : "text-muted-foreground")}>{l}</button>
        ))}
      </div>
      {emails === null ? <Skeleton className="h-64" /> : emails.length === 0 ? (
        <p className="text-sm text-muted-foreground">No emails here yet.</p>
      ) : (
        <ul className="space-y-3">
          {emails.map((e) => (
            <li key={e.id}>
              <Card className="gap-2 p-4">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant="outline">{KIND[e.kind]}</Badge>
                  <Badge variant={e.status === "sent" ? "default" : e.status === "failed" ? "destructive" : "secondary"}
                    className="capitalize">{e.status}</Badge>
                  <Badge variant="outline" className={e.template ? "border-amber-300 bg-amber-50 text-amber-800" : ""}>{e.label}</Badge>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {new Date(e.sent_at ?? e.created_at).toLocaleString()}
                  </span>
                </div>
                <dl className="grid grid-cols-[8rem_1fr] gap-x-3 text-xs">
                  <dt className="text-muted-foreground">Candidate</dt><dd>{e.candidate_name}</dd>
                  <dt className="text-muted-foreground">Intended recipient</dt><dd>{e.intended_to ?? "no address on file"}</dd>
                  <dt className="text-muted-foreground">Actual recipient</dt><dd>{e.actual_to ?? "—"}{e.provider && ` (${e.provider})`}</dd>
                  {e.attachments?.length > 0 && <><dt className="text-muted-foreground">Attachments</dt>
                    <dd>{e.attachments.map((a) => `📎 ${a.filename} (${a.content_type.split(";")[0]})`).join(", ")}</dd></>}
                  {e.error && <><dt className="text-muted-foreground">Error</dt><dd className="text-red-700">{e.error}</dd></>}
                </dl>
                <details>
                  <summary className="cursor-pointer text-sm font-medium">{e.subject}</summary>
                  <pre className="mt-2 rounded-md border bg-white p-3 font-[inherit] text-sm whitespace-pre-wrap">{e.body}</pre>
                </details>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
