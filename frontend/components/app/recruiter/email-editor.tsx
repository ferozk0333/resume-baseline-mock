"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, Mail } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { get, type Email, type EmailConfig } from "@/lib/api";

export const words = (t: string) => (t.match(/\b[\w'’-]+\b/g) ?? []).length;

/** Editable draft: subject, body, who drafted it, word count against the limit, and check results. */
export function EmailEditor({ email, subject, body, onSubject, onBody, limit }: {
  email: Email; subject: string; body: string; onSubject: (s: string) => void; onBody: (s: string) => void;
  limit: number;
}) {
  const n = words(body);
  const issues = [...(email.checks.errors ?? []), ...(email.checks.warnings ?? [])];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline" className={email.template ? "border-amber-300 bg-amber-50 text-amber-800" : ""}>
          {email.label}
        </Badge>
        {email.intended_to && <span className="text-muted-foreground">To: {email.intended_to}</span>}
        <span className={n > limit ? "ml-auto font-medium text-red-700" : "ml-auto text-muted-foreground"}>
          {n} / {limit} words
        </span>
      </div>
      <Input aria-label="Subject" value={subject} onChange={(e) => onSubject(e.target.value)} className="bg-white" />
      <Textarea aria-label="Email body" value={body} onChange={(e) => onBody(e.target.value)}
        className="min-h-56 bg-white font-[inherit] text-sm leading-relaxed" />
      {issues.length > 0 && (
        <p className="flex gap-1.5 text-xs text-red-700"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          {issues.join("; ")}</p>
      )}
      {email.template && (email.checks.model_errors?.length ?? 0) > 0 && (
        <p className="text-xs text-muted-foreground">
          Template used because the model draft {email.checks.model_errors!.some((e) => e.startsWith("model unavailable"))
            ? "was unavailable" : "failed the checks twice"}.
        </p>
      )}
    </div>
  );
}

/** Where a sent email goes: outbox only, and the demo redirect. */
export function SendNotice() {
  const [cfg, setCfg] = useState<EmailConfig | null>(null);
  useEffect(() => { get<EmailConfig>("/email/config").then(setCfg).catch(() => {}); }, []);
  if (!cfg) return null;
  return (
    <p className="flex items-start gap-1.5 rounded-md bg-sky-50 px-2.5 py-1.5 text-xs text-sky-900">
      <Mail className="mt-0.5 size-3.5 shrink-0" />
      <span>
        {cfg.provider === "outbox" ? "Outbox mode: sent emails are stored and shown on the Outbox page; nothing leaves this machine."
          : "SMTP: Send delivers the email."}
        {cfg.demo_redirect && <> Demo redirect on: every email goes to {cfg.demo_redirect} only.</>}
      </span>
    </p>
  );
}
