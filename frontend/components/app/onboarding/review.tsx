"use client";
import { useState } from "react";
import { AlertTriangle, ChevronDown, Plus, Trash2, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { patch, type Project, type ProfileResponse, type Role } from "@/lib/api";

type Edit = { op: "add" | "edit" | "delete" | "add_back"; item: string; id?: string; data?: Record<string, unknown> };
const month = (d: string | null) => (d ? d.slice(0, 7) : "");

export function Review({ data, onChange, onConfirm, confirming }: {
  data: ProfileResponse; onChange: (d: ProfileResponse) => void; onConfirm: () => void; confirming: boolean;
}) {
  const cid = data.candidate.id;
  const { roles, projects, claims, certifications } = data.profile;

  async function apply(edit: Edit, ok?: string) {
    try {
      onChange(await patch<ProfileResponse>(`/candidates/${cid}/profile`, edit));
      if (ok) toast.success(ok);
      return true;
    } catch (e) {
      toast.error((e as Error).message);
      return false;
    }
  }

  const skills = claims.filter((c) => c.kind !== "award");
  return (
    <div className="space-y-6">
      {data.warnings.length > 0 && (
        <Alert>
          <AlertTriangle />
          <AlertTitle>Some sources could not be fully read</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </AlertDescription>
        </Alert>
      )}

      <Section title="Roles" description="Your work experience. Edit anything that was read wrongly.">
        {roles.map((r) => <RoleCard key={r.id} role={r} onSave={apply} />)}
        <AddRole onSave={apply} />
      </Section>

      <Section title="Projects" description="Projects, repositories, publications and case studies.">
        {projects.length === 0 && <p className="text-sm text-muted-foreground">No projects found.</p>}
        {projects.map((p) => <ProjectCard key={p.id} project={p} onSave={apply} />)}
        <AddProject onSave={apply} />
      </Section>

      <div className="grid gap-6 md:grid-cols-2">
        <Section title="Skills and tools" description="Named in your documents.">
          <div className="flex flex-wrap gap-1.5">
            {skills.map((c) => (
              <Badge key={c.id} variant="secondary" className="gap-1 pr-1">
                <span className="text-muted-foreground">{c.kind}</span> {c.name_raw}
                {c.bullet_id && <span className="text-xs text-emerald-700">· in a bullet</span>}
                <button type="button" aria-label={`Remove ${c.name_raw}`} className="rounded p-0.5 hover:bg-white"
                  onClick={() => apply({ op: "delete", item: "claim", id: c.id })}><X className="size-3" /></button>
              </Badge>
            ))}
          </div>
          <AddClaim onSave={apply} />
        </Section>

        <Section title="Certifications" description="A credential ID or link counts as verified.">
          {certifications.length === 0 && <p className="text-sm text-muted-foreground">None.</p>}
          <ul className="space-y-2">
            {certifications.map((c) => (
              <li key={c.id} className="flex items-start justify-between gap-2 rounded-lg border bg-white px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {[c.issuer, c.credential_id || c.verify_url ? "verified" : "not verifiable yet"].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <Button variant="ghost" size="icon" aria-label={`Remove ${c.name}`}
                  onClick={() => apply({ op: "delete", item: "certification", id: c.id })}><Trash2 /></Button>
              </li>
            ))}
          </ul>
          <AddCertification onSave={apply} />
        </Section>
      </div>

      <Section title={`Dropped items (${data.dropped.length})`}
        description="Read from your documents, but the exact text was not found in the source, so they were left out. Add back anything that is correct.">
        {data.dropped.length === 0 ? <p className="text-sm text-muted-foreground">Nothing was dropped.</p> : (
          <ul className="space-y-2">
            {data.dropped.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 rounded-lg border bg-white px-3 py-2 text-sm">
                <span><Badge variant="outline" className="mr-2">{d.item_type}</Badge>{d.text}</span>
                <Button variant="outline" size="sm" onClick={() => apply({ op: "add_back", item: "dropped", id: d.id }, "Added back")}>
                  <Undo2 /> Add back
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Source text" description="Each document as the model read it, after clean-up. Quotes are checked against this text.">
        {data.sources.map((s) => (
          <details key={s.id} className="rounded-lg border bg-white">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
              {{ resume: "Resume", linkedin_pdf: "LinkedIn PDF", portfolio: "Portfolio" }[s.type] ?? s.type}
              {s.status === "failed" && <span className="ml-2 text-destructive">could not be extracted</span>}
            </summary>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap border-t px-3 py-2 text-xs text-muted-foreground">{s.text}</pre>
          </details>
        ))}
      </Section>

      <div className="sticky bottom-0 -mx-6 flex items-center justify-end gap-3 border-t bg-background/95 px-6 py-4 backdrop-blur">
        {data.blocker && <p className="mr-auto text-sm text-destructive">{data.blocker}</p>}
        <Button size="lg" onClick={onConfirm} disabled={!!data.blocker || confirming}>Confirm &amp; score</Button>
      </div>
    </div>
  );
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

type Save = (e: Edit, ok?: string) => Promise<boolean>;

function ReviewFlags({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-amber-800">
      {reasons.map((r, i) => <li key={i} className="flex gap-1"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{r}</li>)}
    </ul>
  );
}

function ItemShell({ title, subtitle, reasons, quote, children, editing, setEditing, onDelete, edited }: {
  title: string; subtitle: string; reasons: string[]; quote: string | null; children: React.ReactNode;
  editing: boolean; setEditing: (b: boolean) => void; onDelete: () => void; edited: boolean;
}) {
  const [open, setOpen] = useState(reasons.length > 0);
  return (
    <div className={cn("rounded-lg border bg-white", reasons.length > 0 && "border-amber-300 bg-amber-50/40")}>
      <div className="flex items-start gap-2 px-3 py-2">
        <button type="button" className="flex flex-1 items-start gap-2 text-left" onClick={() => setOpen(!open)}
          aria-expanded={open}>
          <ChevronDown className={cn("mt-0.5 size-4 shrink-0 transition-transform", !open && "-rotate-90")} />
          <span>
            <span className="text-sm font-medium">{title}</span>
            {edited && <Badge variant="outline" className="ml-2">edited</Badge>}
            <span className="block text-xs text-muted-foreground">{subtitle}</span>
            <ReviewFlags reasons={reasons} />
          </span>
        </button>
        <Button variant="ghost" size="sm" onClick={() => { setEditing(!editing); setOpen(true); }}>
          {editing ? "Cancel" : "Edit"}
        </Button>
        <Button variant="ghost" size="icon" aria-label={`Delete ${title}`} onClick={onDelete}><Trash2 /></Button>
      </div>
      {open && <div className="border-t px-3 py-3">
        {children}
        {quote && !editing && (
          <p className="mt-3 border-l-2 pl-2 text-xs text-muted-foreground">Source: “{quote}”</p>
        )}
      </div>}
    </div>
  );
}

function RoleFields({ initial, onSubmit, submitLabel }: {
  initial: Partial<Role>; onSubmit: (data: Record<string, unknown>) => Promise<boolean>; submitLabel: string;
}) {
  const [f, setF] = useState({
    title: initial.title ?? "", employer: initial.employer ?? "", start_date: month(initial.start_date ?? null),
    end_date: month(initial.end_date ?? null), is_current: initial.is_current ?? false,
    bullets: (initial.bullets ?? []).map((b) => b.text).join("\n"),
  });
  const id = initial.id ?? "new-role";
  return (
    <form className="grid gap-3 md:grid-cols-2" onSubmit={async (e) => {
      e.preventDefault();
      await onSubmit({ ...f, start_date: f.start_date || null, end_date: f.is_current ? null : f.end_date || null,
        bullets: f.bullets.split("\n").map((b) => b.trim()).filter(Boolean) });
    }}>
      <Field id={`${id}-title`} label="Title"><Input id={`${id}-title`} required value={f.title}
        onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field id={`${id}-employer`} label="Employer"><Input id={`${id}-employer`} value={f.employer}
        onChange={(e) => setF({ ...f, employer: e.target.value })} /></Field>
      <Field id={`${id}-start`} label="Start (month)"><Input id={`${id}-start`} type="month" value={f.start_date}
        onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
      <Field id={`${id}-end`} label="End (month)">
        <div className="flex items-center gap-3">
          <Input id={`${id}-end`} type="month" value={f.end_date} disabled={f.is_current}
            onChange={(e) => setF({ ...f, end_date: e.target.value })} />
          <label className="flex items-center gap-1.5 text-sm whitespace-nowrap">
            <input type="checkbox" checked={f.is_current} onChange={(e) => setF({ ...f, is_current: e.target.checked })} />
            Current
          </label>
        </div>
      </Field>
      <div className="md:col-span-2">
        <Field id={`${id}-bullets`} label="Bullets (one per line)">
          <Textarea id={`${id}-bullets`} rows={5} value={f.bullets} onChange={(e) => setF({ ...f, bullets: e.target.value })} />
        </Field>
      </div>
      <div className="md:col-span-2"><Button type="submit">{submitLabel}</Button></div>
    </form>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>;
}

function RoleCard({ role, onSave }: { role: Role; onSave: Save }) {
  const [editing, setEditing] = useState(false);
  const dates = [month(role.start_date) || "?", role.is_current ? "present" : month(role.end_date) || "?"].join(" – ");
  return (
    <ItemShell title={role.employer ? `${role.title} — ${role.employer}` : role.title}
      subtitle={`${dates} · ${role.bullets.length} bullet${role.bullets.length === 1 ? "" : "s"}`}
      reasons={role.review_reasons} quote={role.quote} editing={editing} setEditing={setEditing}
      edited={role.source === "user_edit"} onDelete={() => onSave({ op: "delete", item: "role", id: role.id })}>
      {editing ? (
        <RoleFields initial={role} submitLabel="Save role"
          onSubmit={async (d) => { const ok = await onSave({ op: "edit", item: "role", id: role.id, data: d }, "Role saved");
            if (ok) setEditing(false); return ok; }} />
      ) : (
        <ul className="list-disc space-y-1 pl-5 text-sm">{role.bullets.map((b) => <li key={b.id}>{b.text}</li>)}</ul>
      )}
    </ItemShell>
  );
}

function AddRole({ onSave }: { onSave: Save }) {
  const [open, setOpen] = useState(false);
  if (!open) return <Button variant="outline" size="sm" onClick={() => setOpen(true)}><Plus /> Add a role</Button>;
  return (
    <div className="rounded-lg border border-dashed bg-white p-3">
      <RoleFields initial={{}} submitLabel="Add role"
        onSubmit={async (d) => { const ok = await onSave({ op: "add", item: "role", data: d }, "Role added");
          if (ok) setOpen(false); return ok; }} />
    </div>
  );
}

const PROJECT_KINDS = ["project", "hackathon", "publication", "case_study", "presentation", "work_sample"];

function ProjectFields({ initial, onSubmit, submitLabel }: {
  initial: Partial<Project>; onSubmit: (d: Record<string, unknown>) => Promise<boolean>; submitLabel: string;
}) {
  const [f, setF] = useState({ kind: initial.kind ?? "project", name: initial.name ?? "",
    description: initial.description ?? "", url: initial.url ?? "", end_date: month(initial.end_date ?? null),
    bullets: (initial.bullets ?? []).map((b) => b.text).join("\n") });
  const id = initial.id ?? "new-project";
  return (
    <form className="grid gap-3 md:grid-cols-2" onSubmit={async (e) => {
      e.preventDefault();
      await onSubmit({ ...f, end_date: f.end_date || null, bullets: f.bullets.split("\n").map((b) => b.trim()).filter(Boolean) });
    }}>
      <Field id={`${id}-name`} label="Name"><Input id={`${id}-name`} required value={f.name}
        onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field id={`${id}-kind`} label="Kind">
        <select id={`${id}-kind`} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}
          className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm">
          {PROJECT_KINDS.map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}
        </select>
      </Field>
      <div className="md:col-span-2"><Field id={`${id}-desc`} label="Description">
        <Input id={`${id}-desc`} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field></div>
      <Field id={`${id}-url`} label="Link"><Input id={`${id}-url`} value={f.url}
        onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
      <Field id={`${id}-end`} label="Finished (month)"><Input id={`${id}-end`} type="month" value={f.end_date}
        onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
      <div className="md:col-span-2"><Field id={`${id}-bullets`} label="Bullets (one per line)">
        <Textarea id={`${id}-bullets`} rows={4} value={f.bullets} onChange={(e) => setF({ ...f, bullets: e.target.value })} />
      </Field></div>
      <div className="md:col-span-2"><Button type="submit">{submitLabel}</Button></div>
    </form>
  );
}

function ProjectCard({ project: p, onSave }: { project: Project; onSave: Save }) {
  const [editing, setEditing] = useState(false);
  const github = p.own_commits !== null && p.own_commits !== undefined;
  const fork = p.is_fork && !p.own_commits;
  return (
    <ItemShell title={p.name}
      subtitle={[p.kind.replace("_", " "), github && "GitHub", fork && "fork without own commits (not counted)",
        `${p.bullets.length} bullet${p.bullets.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}
      reasons={p.review_reasons} quote={p.quote} editing={editing} setEditing={setEditing}
      edited={p.source === "user_edit"} onDelete={() => onSave({ op: "delete", item: "project", id: p.id })}>
      {editing ? (
        <ProjectFields initial={p} submitLabel="Save project"
          onSubmit={async (d) => { const ok = await onSave({ op: "edit", item: "project", id: p.id, data: d }, "Project saved");
            if (ok) setEditing(false); return ok; }} />
      ) : (
        <div className="space-y-2 text-sm">
          {p.description && <p className="text-muted-foreground">{p.description}</p>}
          <ul className="list-disc space-y-1 pl-5">{p.bullets.map((b) => <li key={b.id}>{b.text}</li>)}</ul>
        </div>
      )}
    </ItemShell>
  );
}

function AddProject({ onSave }: { onSave: Save }) {
  const [open, setOpen] = useState(false);
  if (!open) return <Button variant="outline" size="sm" onClick={() => setOpen(true)}><Plus /> Add a project</Button>;
  return (
    <div className="rounded-lg border border-dashed bg-white p-3">
      <ProjectFields initial={{}} submitLabel="Add project"
        onSubmit={async (d) => { const ok = await onSave({ op: "add", item: "project", data: d }, "Project added");
          if (ok) setOpen(false); return ok; }} />
    </div>
  );
}

function AddClaim({ onSave }: { onSave: Save }) {
  const [kind, setKind] = useState("skill");
  const [name, setName] = useState("");
  return (
    <form className="flex gap-2" onSubmit={async (e) => {
      e.preventDefault();
      if (name.trim() && await onSave({ op: "add", item: "claim", data: { kind, name: name.trim() } })) setName("");
    }}>
      <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}
        className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm">
        <option value="skill">skill</option><option value="tool">tool</option><option value="award">award</option>
      </select>
      <Input aria-label="Skill or tool name" placeholder="e.g. SQL" value={name} onChange={(e) => setName(e.target.value)} />
      <Button type="submit" variant="outline" disabled={!name.trim()}>Add</Button>
    </form>
  );
}

function AddCertification({ onSave }: { onSave: Save }) {
  const [f, setF] = useState({ name: "", issuer: "", credential_id: "", verify_url: "" });
  return (
    <form className="grid grid-cols-2 gap-2" onSubmit={async (e) => {
      e.preventDefault();
      if (f.name.trim() && await onSave({ op: "add", item: "certification", data: f }, "Certification added"))
        setF({ name: "", issuer: "", credential_id: "", verify_url: "" });
    }}>
      <Input aria-label="Certification name" placeholder="Certification" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <Input aria-label="Issuer" placeholder="Issuer" value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} />
      <Input aria-label="Credential ID" placeholder="Credential ID" value={f.credential_id}
        onChange={(e) => setF({ ...f, credential_id: e.target.value })} />
      <Input aria-label="Verify URL" placeholder="Verify URL" value={f.verify_url}
        onChange={(e) => setF({ ...f, verify_url: e.target.value })} />
      <Button type="submit" variant="outline" size="sm" className="col-span-2 justify-self-start" disabled={!f.name.trim()}>
        <Plus /> Add certification
      </Button>
    </form>
  );
}
