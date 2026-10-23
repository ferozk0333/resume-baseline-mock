"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { FileDrop } from "@/components/ui/file-drop";
import { useRouter } from "next/navigation";
import { ArrowLeft, Copy, FilePlus2, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Stepper } from "@/components/app/jobs/job-form";
import { get, post, useUploadLimit, type JobForm, type JobTemplate } from "@/lib/api";

/** Create job, step 1 (specs/14 §3): from a job description, a copy of an existing job, or from scratch. */
export default function NewJob() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const maxBytes = useUploadLimit();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [templates, setTemplates] = useState<JobTemplate[]>([]);
  const [copyFrom, setCopyFrom] = useState("");
  useEffect(() => { get<JobTemplate[]>("/jobs/templates").then(setTemplates).catch(() => {}); }, []);

  const open = (f: JobForm) => router.push(`/recruiter/jobs/${f.job_id}/edit`);
  async function go(kind: string, p: () => Promise<JobForm>) {
    setBusy(kind);
    try { open(await p()); } catch (e) { toast.error((e as Error).message); setBusy(null); }
  }
  function readJd() {
    const form = new FormData();
    if (file) form.append("file", file);
    else form.append("text", text);
    go("jd", () => post<JobForm>("/jobs/drafts/read", form));
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Link href="/recruiter" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Jobs
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Create job</h1>
        <Stepper step={1} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <FileText className="size-5 text-primary" />
            <CardTitle className="text-base">Upload or paste a job description</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <FileDrop id="jd-file" label="File" what="job description" accept={[".pdf", ".docx", ".txt", ".md"]}
              typesLabel="PDF, DOCX or text" file={file} onFile={setFile} maxBytes={maxBytes} />
            <div className="space-y-1">
              <Label htmlFor="jd-text">Or paste the text</Label>
              <Textarea id="jd-text" value={text} onChange={(e) => setText(e.target.value)} disabled={!!file}
                placeholder="Paste the job description" className="min-h-32 bg-white text-sm" />
            </div>
            <Button disabled={busy !== null || (!file && text.trim().length < 200)} onClick={readJd}>
              {busy === "jd" ? <><Loader2 className="animate-spin" /> Reading…</> : "Read job description"}
            </Button>
            {busy === "jd" && <p className="text-xs text-muted-foreground">Usually 10–30 seconds.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <Copy className="size-5 text-primary" />
            <CardTitle className="text-base">Copy an existing job</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <select aria-label="Job to copy" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}
              className="h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm">
              <option value="">Choose a job or template…</option>
              <optgroup label="Jobs">
                {templates.filter((t) => t.kind === "job").map((t) => <option key={t.id} value={`job:${t.id}`}>{t.title} · {t.company}</option>)}
              </optgroup>
              <optgroup label="Role templates">
                {templates.filter((t) => t.kind === "template" && t.company !== "Market template").map((t) =>
                  <option key={t.id} value={`template:${t.id}`}>{t.title} ({t.level})</option>)}
              </optgroup>
              {templates.some((t) => t.company === "Market template") && (
                <optgroup label="Market templates (from job postings)">
                  {templates.filter((t) => t.company === "Market template").map((t) =>
                    <option key={t.id} value={`template:${t.id}`}>{t.title}</option>)}
                </optgroup>
              )}
            </select>
            <Button variant="outline" disabled={!copyFrom || busy !== null}
              onClick={() => { const [kind, id] = copyFrom.split(/:(.*)/); go("copy", () => post<JobForm>("/jobs/drafts/copy", { kind, id })); }}>
              Copy and edit
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <FilePlus2 className="size-5 text-primary" />
            <CardTitle className="text-base">Start from scratch</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="outline" disabled={busy !== null} onClick={() => go("scratch", () => post<JobForm>("/jobs/drafts"))}>
              Start from scratch
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
