"use client";
import { useState } from "react";
import { CalendarCheck, Check, Inbox, Loader2, MessageSquareReply, Paperclip, Send, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EmailEditor, SendNotice } from "@/components/app/recruiter/email-editor";
import { cn } from "@/lib/utils";
import type { FeedbackSummary, Message, Reading, Scorecard, WorkflowStep, WorkflowView } from "@/lib/api";

export type Act = (body: Record<string, unknown>, label?: string) => Promise<void>;

const LABEL: Record<Reading["label"], string> = { slot_chosen: "Slot chosen", times_proposed: "Times proposed",
  question: "Question", decline: "Declines / withdraws", unclear: "Unclear" };
const REC: Record<string, string> = { strong_yes: "Strong yes", yes: "Yes", no: "No", strong_no: "Strong no" };
const LIMIT: Record<string, number> = { not_moving_forward: 150 };
const fmt = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

/** The one thing that needs attention now, with the agent's draft or suggestion and the action buttons. */
export function StepCard({ v, act, busy, onSaveEmail, onReply, onScorecard }: {
  v: WorkflowView; act: Act; busy: string | null;
  onSaveEmail: (id: string, subject: string, body: string) => Promise<void>;
  onReply: (body: { text?: string; canned?: string }) => Promise<void>;
  onScorecard: (body: Record<string, unknown>) => Promise<void>;
}) {
  const s = v.step!;
  const name = v.application.name ?? "the candidate";
  return (
    <section aria-label="Current step" className="space-y-4 rounded-xl border bg-white p-5">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{s.title}</h2>
        <Badge variant={s.status === "needs_action" ? "default" : "secondary"}>{v.workflow?.status_label}</Badge>
        {s.since && <span className="ml-auto text-xs text-muted-foreground">since {fmt(s.since)}</span>}
      </header>
      {s.unread > 0 && s.step !== "await_reply" && (
        <p className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <Inbox className="size-4" /> A new reply from {name} is waiting.
          <Button size="xs" variant="outline" className="ml-auto" onClick={() => act({ action: "choose", option: "wait" })}>Read it now</Button>
        </p>
      )}
      {s.step === "approve_email" && <Approve key={`${s.email?.id}-${s.email?.subject}`} s={s} act={act} busy={busy} onSave={onSaveEmail} />}
      {s.step === "await_reply" && <AwaitReply v={v} act={act} busy={busy} onReply={onReply} />}
      {s.step === "confirm_slot" && <ConfirmSlot s={s} v={v} act={act} busy={busy} />}
      {s.step === "scheduled" && (
        <div className="space-y-3 text-sm">
          <p className="flex items-center gap-2"><CalendarCheck className="size-4 text-emerald-600" />
            {s.interview ? <>{s.interview.stage} on <b>{s.interview.label}</b>. The invite (.ics) went with the confirmation.</>
              : "The interview was arranged outside RightStaff."}</p>
          <p className="text-muted-foreground">When the interview time passes, the workflow asks for feedback.</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => act({ action: "done" })}>Interview done: enter feedback</Button>
            <Button size="sm" variant="outline" onClick={() => act({ action: "reschedule" })}>Reschedule</Button>
          </div>
        </div>
      )}
      {s.step === "enter_feedback" && <Feedback s={s} act={act} busy={busy} onScorecard={onScorecard} />}
      {s.step === "decide" && <Decide s={s} act={act} busy={busy} />}
      {s.step === "confirm_withdrawal" && (
        <div className="space-y-3">
          {s.reply && <ReplyBox m={s.reply} />}
          <p className="text-sm">The reply reads as a withdrawal. Confirm to close the workflow (moves to Not moving forward, no email).</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => act({ action: "confirm" })}>Confirm withdrawal</Button>
            <Button size="sm" variant="outline" onClick={() => act({ action: "not_withdrawal" })}>Not a withdrawal</Button>
          </div>
        </div>
      )}
      {s.step === "choose" && (
        <div className="space-y-3">
          {s.reply && <ReplyBox m={s.reply} />}
          <p className="text-sm text-muted-foreground">Nothing is sent until you approve an email.</p>
          <div className="flex flex-wrap gap-2">
            {s.options?.map((o, i) => (
              <Button key={o.key} size="sm" variant={i === 0 ? "default" : "outline"} disabled={busy !== null}
                onClick={() => act({ action: "choose", option: o.key })}>{o.label}</Button>
            ))}
          </div>
        </div>
      )}
      {!["choose", "decide"].includes(s.step) && (
        <div className="flex items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
          <Label htmlFor="other">Other next step</Label>
          <select id="other" value="" disabled={busy !== null} onChange={(e) => e.target.value && act({ action: "choose", option: e.target.value })}
            className="h-7 rounded-md border border-field-border bg-field px-2 text-xs">
            <option value="">Choose…</option>
            <option value="slots">Propose interview slots</option>
            <option value="answer">Draft an answer</option>
            <option value="reject">Draft the not-moving-forward email</option>
            <option value="close">Close without an email</option>
          </select>
        </div>
      )}
    </section>
  );
}

function Approve({ s, act, busy, onSave }: { s: WorkflowStep; act: Act; busy: string | null;
  onSave: (id: string, subject: string, body: string) => Promise<void> }) {
  const e = s.email!;
  const [subject, setSubject] = useState(e.subject);
  const [body, setBody] = useState(e.body);
  async function send() {
    await onSave(e.id, subject, body);
    await act({ action: "send", email_id: e.id }, "send");
  }
  return (
    <div className="space-y-3">
      {s.error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">The last send failed: {s.error}</p>}
      <EmailEditor email={e} subject={subject} body={body} onSubject={setSubject} onBody={setBody}
        limit={LIMIT[e.kind] ?? 120} />
      {e.attachments?.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Paperclip className="size-3.5" />
          {e.attachments.map((a) => `${a.filename} (${a.content_type.split(";")[0]})`).join(", ")}</p>
      )}
      <SendNotice />
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => act({ action: "discard" })}><Trash2 /> Discard</Button>
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => onSave(e.id, subject, body)}>Save draft</Button>
        <Button size="sm" disabled={busy !== null} onClick={send}>
          {busy === "send" ? <Loader2 className="animate-spin" /> : <Send />} Approve and send
        </Button>
      </div>
    </div>
  );
}

function AwaitReply({ v, act, busy, onReply }: { v: WorkflowView; act: Act; busy: string | null;
  onReply: (body: { text?: string; canned?: string }) => Promise<void> }) {
  const [text, setText] = useState("");
  const s = v.step!;
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground">
        Waiting for {v.application.name ?? "the candidate"}. {s.due && <>A nudge will be drafted for your approval on {fmt(s.due)} if there is no reply.</>}
      </p>
      <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
        <Label htmlFor="reply" className="flex items-center gap-1.5"><MessageSquareReply className="size-4" /> Simulate / paste the candidate&apos;s reply</Label>
        <Textarea id="reply" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the reply here"
          className="min-h-20 bg-white" />
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Canned:</span>
          {v.canned_replies.map((c) => (
            <Button key={c.key} size="xs" variant="outline" disabled={busy !== null} title={c.text}
              onClick={() => onReply({ canned: c.key })}>{c.text.length > 32 ? `${c.text.slice(0, 30)}…` : c.text}</Button>
          ))}
          <Button size="sm" className="ml-auto" disabled={!text.trim() || busy !== null}
            onClick={() => onReply({ text }).then(() => setText(""))}>Add reply</Button>
        </div>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act({ action: "nudge" })}>Draft a nudge now</Button>
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act({ action: "propose" })}>Propose slots now</Button>
      </div>
    </div>
  );
}

export function ReplyBox({ m }: { m: Message }) {
  const r = m.reading;
  return (
    <div className="space-y-1.5 rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>Reply · {fmt(m.created_at)} · {m.source}</span>
        {r && <Badge variant="secondary">{LABEL[r.label]}</Badge>}
        {r && <span>{r.method === "model" ? `read by ${r.model}` : "read by code rules"}</span>}
      </div>
      <p className="whitespace-pre-wrap">{m.body}</p>
      {r && <p className="text-xs text-muted-foreground">Because: “{r.quote}”{r.question ? ` · Question: ${r.question}` : ""}</p>}
      {r?.notes?.map((n) => <p key={n} className="text-xs text-amber-700">{n}</p>)}
    </div>
  );
}

function ConfirmSlot({ s, v, act, busy }: { s: WorkflowStep; v: WorkflowView; act: Act; busy: string | null }) {
  const slots = s.slots ?? [];
  const [pick, setPick] = useState(slots.find((x) => x.fits)?.start ?? "");
  const [custom, setCustom] = useState("");
  const tz = slots[0]?.timezone ?? "America/New_York";
  function customIso() {
    if (!custom) return null;
    const sample = slots[0]?.start.slice(-6) ?? "-04:00";        // the window's offset (shown below)
    return `${custom}:00${sample}`;
  }
  return (
    <div className="space-y-3">
      {s.reply && <ReplyBox m={s.reply} />}
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-sm font-medium">Proposed slots ({v.settings.duration_minutes} min, from “{v.settings.availability}”)</legend>
        {slots.map((x) => (
          <label key={x.start} className={cn("flex items-center gap-2 rounded-md border px-3 py-2 text-sm", !x.fits && "opacity-60",
            pick === x.start && "border-primary bg-primary/5")}>
            <input type="radio" name="slot" value={x.start} checked={pick === x.start} disabled={!x.fits}
              onChange={() => { setPick(x.start); setCustom(""); }} />
            <span className="font-medium">{x.label}</span>
            {x.source === "candidate" && <Badge variant="outline" className="text-[10px]">candidate&apos;s time</Badge>}
            {x.note && <span className="text-xs text-amber-700">{x.note}</span>}
          </label>
        ))}
        {slots.length === 0 && <p className="text-sm text-muted-foreground">No slots could be read from the availability windows. Enter a time.</p>}
        <label className="flex items-center gap-2 text-sm">
          <input type="radio" name="slot" checked={!!custom} onChange={() => setPick("")} />
          Another time:
          <Input type="datetime-local" aria-label="Another time" value={custom} onChange={(e) => { setCustom(e.target.value); setPick(""); }}
            className="h-8 w-56 bg-white" />
          <span className="text-xs text-muted-foreground">{tz}</span>
        </label>
      </fieldset>
      <div className="flex justify-end">
        <Button size="sm" disabled={busy !== null || !(pick || custom)}
          onClick={() => act({ action: "confirm", start: pick || customIso() })}>
          <Check /> Confirm slot and draft the confirmation
        </Button>
      </div>
    </div>
  );
}

function Feedback({ s, act, busy, onScorecard }: { s: WorkflowStep; act: Act; busy: string | null;
  onScorecard: (body: Record<string, unknown>) => Promise<void> }) {
  const areas = s.focus?.length ? s.focus : ["Overall"];
  const blank = () => ({ interviewer: "", notes: "", recommendation: "yes",
    ratings: Object.fromEntries(areas.map((a) => [a, { rating: 3, notes: "" }])) as Record<string, { rating: number; notes: string }> });
  const [form, setForm] = useState(blank);
  const cards = s.scorecards ?? [];
  async function add() {
    await onScorecard({ interviewer: form.interviewer, notes: form.notes, recommendation: form.recommendation,
      ratings: areas.map((a) => ({ area: a, rating: form.ratings[a].rating, notes: form.ratings[a].notes })) });
    setForm(blank());
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{s.stage}: one scorecard per interviewer, each focus area rated 1–4.</p>
      {cards.length > 0 && <Scorecards cards={cards} />}
      <form aria-label="Scorecard" className="space-y-2 rounded-lg border bg-muted/30 p-3" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <div className="flex gap-2">
          <Input aria-label="Interviewer" placeholder="Interviewer name" value={form.interviewer}
            onChange={(e) => setForm({ ...form, interviewer: e.target.value })} className="h-8 bg-white" />
          <select aria-label="Recommendation" value={form.recommendation} onChange={(e) => setForm({ ...form, recommendation: e.target.value })}
            className="h-8 rounded-md border border-field-border bg-field px-2 text-sm">
            {Object.entries(REC).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        {areas.map((a) => (
          <div key={a} className="grid grid-cols-[minmax(0,10rem)_5rem_1fr] items-center gap-2 text-sm">
            <span className="truncate" title={a}>{a}</span>
            <select aria-label={`${a} rating`} value={form.ratings[a].rating}
              onChange={(e) => setForm({ ...form, ratings: { ...form.ratings, [a]: { ...form.ratings[a], rating: Number(e.target.value) } } })}
              className="h-8 rounded-md border border-field-border bg-field px-2">
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} / 4</option>)}
            </select>
            <Input aria-label={`${a} notes`} placeholder="Notes" value={form.ratings[a].notes}
              onChange={(e) => setForm({ ...form, ratings: { ...form.ratings, [a]: { ...form.ratings[a], notes: e.target.value } } })}
              className="h-8 bg-white" />
          </div>
        ))}
        <Textarea aria-label="Overall notes" placeholder="Overall notes" value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })} className="min-h-14 bg-white text-sm" />
        <div className="flex justify-end"><Button size="sm" variant="outline" type="submit" disabled={!form.interviewer.trim()}>Add scorecard</Button></div>
      </form>
      <div className="flex justify-end">
        <Button size="sm" disabled={!cards.length || busy !== null} onClick={() => act({ action: "summarize" })}>
          Summarize feedback ({cards.length})
        </Button>
      </div>
    </div>
  );
}

function Scorecards({ cards }: { cards: Scorecard[] }) {
  return (
    <ul className="space-y-2">
      {cards.map((c) => (
        <li key={c.id} className="rounded-lg border p-3 text-sm">
          <div className="flex items-center gap-2"><b>{c.interviewer}</b><Badge variant="outline">{REC[c.recommendation]}</Badge></div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {c.ratings.map((r) => <li key={r.area}><span className="tabular-nums font-medium">{r.rating}/4</span> {r.area}{r.notes && ` · ${r.notes}`}</li>)}
          </ul>
          {c.notes && <p className="mt-1 text-xs text-muted-foreground">{c.notes}</p>}
        </li>
      ))}
    </ul>
  );
}

function Points({ title, points }: { title: string; points: FeedbackSummary["agreements"] }) {
  return (
    <div>
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h4>
      {points.length === 0 ? <p className="text-xs text-muted-foreground">None.</p> : (
        <ul className="mt-1 space-y-1 text-sm">
          {points.map((p, i) => <li key={i}>{p.text}<span className="block text-xs text-muted-foreground">
            {p.quotes.map((q) => `“${q.quote}”`).join(" · ")}</span></li>)}
        </ul>
      )}
    </div>
  );
}

function Decide({ s, act, busy }: { s: WorkflowStep; act: Act; busy: string | null }) {
  const sum = s.summary;
  return (
    <div className="space-y-4">
      {sum && (
        <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{sum.scorecards} scorecard{sum.scorecards === 1 ? "" : "s"} · {sum.method === "model" ? `summarized by ${sum.model}` : "code summary"}</span>
            <span className="ml-auto">Interviewers: {Object.entries(sum.recommendations).filter(([, n]) => n).map(([k, n]) => `${REC[k]} ${n}`).join(", ")}</span>
          </div>
          <Points title="Agreements" points={sum.agreements} />
          <Points title="Disagreements" points={sum.disagreements} />
          <Points title="Open questions" points={sum.open_questions} />
          {sum.notes.length > 0 && <details className="text-xs text-muted-foreground"><summary>Checks ({sum.notes.length})</summary>
            {sum.notes.map((n) => <p key={n}>{n}</p>)}</details>}
          <p className="text-xs text-muted-foreground">The summary makes no recommendation. The decision is yours.</p>
        </div>
      )}
      {s.scorecards && s.scorecards.length > 0 && <Scorecards cards={s.scorecards} />}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy !== null} onClick={() => act({ action: "advance" })}>Advance to {s.next}</Button>
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act({ action: "reject" })}>Not moving forward</Button>
        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => act({ action: "hold" })}>Hold</Button>
      </div>
    </div>
  );
}
