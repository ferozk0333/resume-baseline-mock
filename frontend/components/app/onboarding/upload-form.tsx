"use client";
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { FileDrop } from "@/components/ui/file-drop";
import { post, useUploadLimit } from "@/lib/api";

type Cert = { name: string; issuer: string; credential_id: string; verify_url: string };
type Ref = { name: string; relationship: string; contact: string; statement: string };
const VIDEO = /^(https?:\/\/)?(www\.|m\.)?(youtube\.com\/(watch\?v=|embed\/|shorts\/)[\w-]{11}|youtu\.be\/[\w-]{11}|loom\.com\/(share|embed)\/[0-9a-f]{32})/i;

/** Why each optional source helps (specs/17 §3, step 1). */
function Why({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-medium text-emerald-800">{children}</p>;
}

/** The documents form. Onboarding (specs/17) has no role yet (roles are picked while the documents are read) and
 * may create the candidate on submit (`ensureCandidate`). */
export function UploadForm({ candidateId, ensureCandidate, baselineId, onboarding = false, onStarted }: {
  candidateId?: string; ensureCandidate?: () => Promise<string>; baselineId?: string; onboarding?: boolean;
  onStarted: (taskId: string, candidateId: string) => void;
}) {
  const [resume, setResume] = useState<File | null>(null);
  const [linkedin, setLinkedin] = useState<File | null>(null);
  const [github, setGithub] = useState("");
  const [portfolio, setPortfolio] = useState("");
  const [linkedinUrl, setLinkedinUrl] = useState("");
  const badLinkedin = linkedinUrl.trim() !== "" && !/^(https?:\/\/)?([a-z]{2,3}\.)?linkedin\.com\/in\/[\w\-%]{3,100}\/?$/i.test(linkedinUrl.trim());
  const [videos, setVideos] = useState<string[]>([""]);
  const [certs, setCerts] = useState<Cert[]>([]);
  const [refs, setRefs] = useState<Ref[]>([]);
  const [busy, setBusy] = useState(false);
  const maxBytes = useUploadLimit();
  const badVideos = videos.filter((v) => v.trim() && !VIDEO.test(v.trim()));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!resume) return;
    setBusy(true);
    const form = new FormData();
    if (baselineId) form.append("baseline_id", baselineId);
    if (onboarding) form.append("onboarding", "true");
    form.append("resume", resume);
    if (linkedin) form.append("linkedin_pdf", linkedin);
    if (github.trim()) form.append("github", github.trim());
    if (portfolio.trim()) form.append("portfolio_url", portfolio.trim());
    if (linkedinUrl.trim()) form.append("linkedin_url", linkedinUrl.trim());
    form.append("demo_videos", JSON.stringify(videos.map((v) => v.trim()).filter(Boolean)));
    form.append("certifications", JSON.stringify(certs.filter((c) => c.name.trim()).map((c) => ({
      name: c.name, issuer: c.issuer || null, credential_id: c.credential_id || null, verify_url: c.verify_url || null }))));
    form.append("references", JSON.stringify(refs.filter((r) => r.name.trim()).map((r) => ({
      name: r.name, relationship: r.relationship || null, contact: r.contact || null, statement: r.statement || null }))));
    try {
      const cid = candidateId ?? (await ensureCandidate!());
      const { task_id } = await post<{ task_id: string }>(`/candidates/${cid}/onboard`, form);
      onStarted(task_id, cid);
    } catch (err) {
      toast.error((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Documents</CardTitle>
          <CardDescription>PDF or DOCX. Only the resume is required.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5 md:grid-cols-2">
          <FileDrop id="resume" label="Resume" required what="resume" accept={[".pdf", ".docx"]} typesLabel="PDF or DOCX"
            file={resume} onFile={setResume} maxBytes={maxBytes} />
          <FileDrop id="linkedin" label="LinkedIn profile (PDF)" what="LinkedIn PDF" accept={[".pdf"]} typesLabel="PDF"
            file={linkedin} onFile={setLinkedin} maxBytes={maxBytes} hint={<>
              <Why>Confirms your resume (Trust)</Why>
              <p className="text-xs text-muted-foreground">
                On your LinkedIn profile, open <span className="font-medium">More → Save to PDF</span> and upload that file.
              </p>
            </>} />
          <div className="space-y-1.5">
            <Label htmlFor="linkedin-url">LinkedIn profile URL</Label>
            <Input id="linkedin-url" value={linkedinUrl} onChange={(e) => setLinkedinUrl(e.target.value)}
              placeholder="https://www.linkedin.com/in/your-name" aria-invalid={badLinkedin} />
            <p className={badLinkedin ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
              {badLinkedin ? "Use your profile link: linkedin.com/in/…" : "Optional. Shown to recruiters as a link; not scored."}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="github">GitHub username or profile URL</Label>
            <Input id="github" value={github} onChange={(e) => setGithub(e.target.value)} placeholder="your-username" />
            <Why>Shows real work (Expertise, Trust)</Why>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="portfolio">Portfolio URL</Label>
            <Input id="portfolio" type="url" value={portfolio} onChange={(e) => setPortfolio(e.target.value)}
              placeholder="https://your-site.com" />
            <Why>Shows finished work in context (Expertise)</Why>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Project demo videos</CardTitle>
          <CardDescription>
            Up to 3 YouTube or Loom links, 1–2 minutes each.
          </CardDescription>
          <Why>Lets recruiters see your work for themselves (shown to recruiters, not scored)</Why>
        </CardHeader>
        <CardContent className="space-y-2">
          {videos.map((v, i) => (
            <div key={i} className="flex gap-2">
              <Input aria-label={`Demo video ${i + 1}`} value={v} placeholder="https://youtu.be/…"
                aria-invalid={!!v.trim() && !VIDEO.test(v.trim())}
                onChange={(e) => setVideos(videos.map((x, j) => (j === i ? e.target.value : x)))} />
              {videos.length > 1 && (
                <Button type="button" variant="ghost" size="icon" aria-label="Remove video"
                  onClick={() => setVideos(videos.filter((_, j) => j !== i))}><Trash2 /></Button>
              )}
            </div>
          ))}
          {badVideos.length > 0 && <p className="text-sm text-destructive">Only YouTube and Loom video links work.</p>}
          {videos.length < 3 && (
            <Button type="button" variant="outline" size="sm" onClick={() => setVideos([...videos, ""])}>
              <Plus /> Add a video
            </Button>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Certifications</CardTitle>
            <CardDescription>Adding a credential ID or link makes a certificate count as stronger proof (Trust).</CardDescription>
            <Why>Backs the skills you claim (Expertise, Trust)</Why>
          </CardHeader>
          <CardContent className="space-y-3">
            {certs.map((c, i) => (
              <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border p-3">
                {(["name", "issuer", "credential_id", "verify_url"] as const).map((f) => (
                  <Input key={f} aria-label={`Certification ${i + 1} ${f.replace("_", " ")}`} value={c[f]}
                    placeholder={{ name: "Name", issuer: "Issuer", credential_id: "Credential ID (optional)", verify_url: "Link (optional)" }[f]}
                    onChange={(e) => setCerts(certs.map((x, j) => (j === i ? { ...x, [f]: e.target.value } : x)))} />
                ))}
              </div>
            ))}
            <Button type="button" variant="outline" size="sm"
              onClick={() => setCerts([...certs, { name: "", issuer: "", credential_id: "", verify_url: "" }])}>
              <Plus /> Add a certification
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">References</CardTitle>
            <Why>A manager reference is the strongest proof (Trust)</Why>
          </CardHeader>
          <CardContent className="space-y-3">
            {refs.map((r, i) => (
              <div key={i} className="space-y-2 rounded-lg border p-3">
                <div className="grid grid-cols-3 gap-2">
                  <Input aria-label={`Reference ${i + 1} name`} placeholder="Name" value={r.name}
                    onChange={(e) => setRefs(refs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  <select aria-label={`Reference ${i + 1} relationship`} value={r.relationship}
                    className="h-8 rounded-lg border border-field-border bg-field px-2 text-sm"
                    onChange={(e) => setRefs(refs.map((x, j) => (j === i ? { ...x, relationship: e.target.value } : x)))}>
                    <option value="">Relationship</option>
                    <option value="manager">Manager</option>
                    <option value="supervisor">Supervisor</option>
                    <option value="peer">Peer</option>
                    <option value="other">Other</option>
                  </select>
                  <Input aria-label={`Reference ${i + 1} contact`} placeholder="Contact" value={r.contact}
                    onChange={(e) => setRefs(refs.map((x, j) => (j === i ? { ...x, contact: e.target.value } : x)))} />
                </div>
                <Textarea aria-label={`Reference ${i + 1} statement`} placeholder="What they can confirm" value={r.statement}
                  onChange={(e) => setRefs(refs.map((x, j) => (j === i ? { ...x, statement: e.target.value } : x)))} />
              </div>
            ))}
            <Button type="button" variant="outline" size="sm"
              onClick={() => setRefs([...refs, { name: "", relationship: "", contact: "", statement: "" }])}>
              <Plus /> Add a reference
            </Button>
          </CardContent>
        </Card>
      </div>

      <div className="flex justify-end">
        <Button type="submit" size="lg" disabled={!resume || busy || badVideos.length > 0}>
          Read my documents
        </Button>
      </div>
    </form>
  );
}
