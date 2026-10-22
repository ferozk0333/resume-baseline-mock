"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, CalendarCheck, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Certification, Project, Role } from "@/lib/api";
import { BulletEditor, newBullet, type DraftBullet } from "./bullet-editor";
import { friendlyMonth, friendlyRange, monthValue } from "./dates";
import { useReviewEdit } from "./edit-context";
import { EntryCard, type FieldSpec } from "./entry-card";
import { same, useReviewApi } from "./review-api";
import { ExtLink, LinkedText } from "@/components/app/ext-link";

/** A draft that starts from the saved values whenever the card enters edit mode, and marks the card dirty. */
export function useDraft<T>(id: string, initial: () => T) {
  const { editing, setDirty } = useReviewEdit();
  const [draft, setDraft] = useState<T>(initial);
  const [base, setBase] = useState<T>(initial);
  const isEditing = editing === id;
  useEffect(() => { if (isEditing) setDirty(!same(draft, base)); }, [draft, base, isEditing, setDirty]);
  const reset = () => { const v = initial(); setDraft(v); setBase(v); };
  return { draft, setDraft, reset, isEditing };
}

export const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;
const bulletsFrom = (b: { text: string }[] | undefined) => (b ?? []).map((x) => newBullet(x.text));
const texts = (b: DraftBullet[]) => b.map((x) => x.text.trim()).filter(Boolean);

export function BulletList({ items }: { items: { id: string; text: string }[] }) {
  if (!items.length) return null;
  return <ul className="list-disc space-y-1 pl-4">{items.map((b) => <li key={b.id}><LinkedText text={b.text} /></li>)}</ul>;
}

export function Link({ url }: { url: string | null | undefined }) {
  if (!url) return null;
  return <ExtLink url={url} icon className="break-all">{url}</ExtLink>;
}

/** Year-only and source-conflict notes with "the months are right" (specs/19 items 9, 12). */
function DateNotes({ item, kind, onEdit }: { item: Role | Project; kind: "role" | "project"; onEdit: () => void }) {
  const { apply } = useReviewApi();
  const notes = [item.year_only && "We only found the year. Please check the months.", item.conflict].filter(Boolean) as string[];
  const other = item.review_reasons;
  if (!notes.length && !other.length) return null;
  return (
    <>
      {notes.map((n) => (
        <div key={n} className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-sm text-amber-950">
          <AlertTriangle className="size-4 shrink-0 text-amber-700" aria-hidden />
          <span className="min-w-0 flex-1">{n}</span>
          <Button type="button" size="xs" variant="outline" onClick={onEdit}><Pencil /> Fix dates</Button>
          <Button type="button" size="xs" variant="outline"
            onClick={() => apply({ op: "confirm_dates", item: kind, id: item.id }, "Dates confirmed")}>
            <CalendarCheck /> The months are right
          </Button>
        </div>
      ))}
      {other.map((r) => (
        <p key={r} className="flex gap-1.5 text-xs text-amber-800"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{r}</p>
      ))}
    </>
  );
}

function MonthInput({ id, value, onChange, disabled, label }: { id: string; value: string; onChange: (v: string) => void;
  disabled?: boolean; label?: string }) {
  return <Input id={id} type="month" value={value} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.value)} />;
}

// --- Roles -----------------------------------------------------------------------------------------------------------

type RoleDraft = { title: string; employer: string; location: string; start: string; end: string; current: boolean;
  bullets: DraftBullet[] };

export function RoleCard({ role, onDone }: { role: Role | null; onDone?: () => void }) {
  const api = useReviewApi();
  const { startEdit } = useReviewEdit();
  const id = role?.id ?? "new-role";
  const init = (): RoleDraft => ({ title: role?.title ?? "", employer: role?.employer ?? "", location: role?.location ?? "",
    start: monthValue(role?.start_date), end: monthValue(role?.end_date), current: role?.is_current ?? false,
    bullets: bulletsFrom(role?.bullets) });
  const { draft: f, setDraft: setF, reset } = useDraft(id, init);
  const [saving, setSaving] = useState(false);
  const set = (p: Partial<RoleDraft>) => setF({ ...f, ...p });
  const codes = role?.review_codes ?? [];

  const fields: FieldSpec[] = [
    { key: "title", label: "Title", htmlFor: `${id}-title`, view: role?.title,
      edit: <Input id={`${id}-title`} required value={f.title} placeholder="e.g. Data Analyst" onChange={(e) => set({ title: e.target.value })} /> },
    { key: "employer", label: "Employer", htmlFor: `${id}-employer`, view: role?.employer,
      edit: <Input id={`${id}-employer`} value={f.employer} placeholder="Company or organization" onChange={(e) => set({ employer: e.target.value })} /> },
    { key: "location", label: "Location (optional)", htmlFor: `${id}-location`, view: role?.location,
      edit: <Input id={`${id}-location`} value={f.location} placeholder="City, or Remote" onChange={(e) => set({ location: e.target.value })} /> },
    { key: "start", label: "Start (month)", htmlFor: `${id}-start`, view: friendlyMonth(role?.start_date, codes.includes("year_only_start")),
      edit: <MonthInput id={`${id}-start`} value={f.start} onChange={(v) => set({ start: v })} /> },
    { key: "end", label: "End (month)", htmlFor: `${id}-end`,
      view: role?.is_current ? "Present" : friendlyMonth(role?.end_date, codes.includes("year_only_end")),
      edit: (
        <div className="flex items-center gap-3">
          <MonthInput id={`${id}-end`} value={f.current ? "" : f.end} disabled={f.current} onChange={(v) => set({ end: v })} />
          <label className="flex items-center gap-1.5 text-sm whitespace-nowrap">
            <input type="checkbox" checked={f.current} onChange={(e) => set({ current: e.target.checked })} /> Current
          </label>
        </div>) },
    { key: "bullets", label: "Bullets", wide: true, view: role?.bullets.length ? <BulletList items={role.bullets} /> : null,
      edit: <BulletEditor id={id} bullets={f.bullets} onChange={(b) => set({ bullets: b })} /> },
  ];

  async function save() {
    setSaving(true);
    const out = await api.apply({ op: role ? "edit" : "add", item: "role", id: role?.id, data: {
      title: f.title, employer: f.employer, location: f.location, start_date: f.start || null,
      end_date: f.current ? null : f.end || null, is_current: f.current, bullets: texts(f.bullets) } },
    role ? "Role saved" : "Role added");
    setSaving(false);
    if (out) onDone?.();
    return !!out;
  }

  return (
    <EntryCard id={id} kindLabel="Role" testId="role-card" title={role?.title || "New role"} org={role?.employer}
      dates={role ? friendlyRange(role.start_date, role.end_date, role.is_current, codes) : undefined}
      meta={role ? plural(role.bullets.length, "bullet") : undefined} sources={role?.sources}
      checkDates={!!role && (role.year_only || !!role.conflict || role.review_reasons.length > 0)}
      edited={role?.source === "user_edit" && (role.sources ?? []).some((s) => s !== "Added by you")}
      notes={role && <DateNotes item={role} kind="role" onEdit={async () => { if (await startEdit(id)) reset(); }} />}
      fields={fields} quote={role?.quote} saving={saving} canSave={!!f.title.trim()}
      onEditStart={reset} onSave={save} onCancel={() => { reset(); onDone?.(); }}
      onDelete={role ? () => api.remove({ op: "delete", item: "role", id: role.id }, `${role.title}`) : undefined} />
  );
}

// --- Projects ----------------------------------------------------------------------------------------------------------

export const PROJECT_TYPES: [string, string][] = [["project", "Project"], ["work_sample", "Work sample"],
  ["hackathon", "Hackathon or competition"], ["publication", "Publication"], ["presentation", "Talk or conference"],
  ["case_study", "Case study"]];
const TYPE_LABEL: Record<string, string> = { ...Object.fromEntries(PROJECT_TYPES), repo: "GitHub repository" };

type ProjectDraft = { name: string; kind: string; url: string; start: string; end: string; role_id: string;
  description: string; bullets: DraftBullet[] };

export function ProjectCard({ project: p, onDone }: { project: Project | null; onDone?: () => void }) {
  const api = useReviewApi();
  const { startEdit } = useReviewEdit();
  const id = p?.id ?? "new-project";
  const init = (): ProjectDraft => ({ name: p?.name ?? "", kind: p?.kind ?? "project", url: p?.url ?? "",
    start: monthValue(p?.start_date), end: monthValue(p?.end_date), role_id: p?.role_id ?? "",
    description: p?.description ?? "", bullets: bulletsFrom(p?.bullets) });
  const { draft: f, setDraft: setF, reset } = useDraft(id, init);
  const [saving, setSaving] = useState(false);
  const set = (x: Partial<ProjectDraft>) => setF({ ...f, ...x });
  const github = p?.own_commits !== null && p?.own_commits !== undefined;
  const fork = p?.is_fork && !p?.own_commits;
  const role = api.roles.find((r) => r.id === p?.role_id);
  const codes = p?.review_codes ?? [];

  const fields: FieldSpec[] = [
    { key: "name", label: "Name", htmlFor: `${id}-name`, view: p?.name,
      edit: <Input id={`${id}-name`} required value={f.name} onChange={(e) => set({ name: e.target.value })} /> },
    { key: "kind", label: "Type", htmlFor: `${id}-kind`, view: TYPE_LABEL[p?.kind ?? ""] ?? p?.kind,
      edit: f.kind === "repo" ? <p className="py-1.5 text-sm text-muted-foreground">GitHub repository</p> : (
        <select id={`${id}-kind`} value={f.kind} onChange={(e) => set({ kind: e.target.value })}
          className="h-8 w-full rounded-lg border border-field-border bg-field px-2 text-sm">
          {PROJECT_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>) },
    { key: "url", label: "Link", htmlFor: `${id}-url`, view: p?.url ? <Link url={p.url} /> : null,
      edit: <Input id={`${id}-url`} value={f.url} placeholder="https://" onChange={(e) => set({ url: e.target.value })} /> },
    { key: "role", label: "Related role (optional)", htmlFor: `${id}-role`, view: role ? `${role.title}${role.employer ? ` — ${role.employer}` : ""}` : null,
      edit: (
        <select id={`${id}-role`} value={f.role_id} onChange={(e) => set({ role_id: e.target.value })}
          className="h-8 w-full rounded-lg border border-field-border bg-field px-2 text-sm">
          <option value="">None</option>
          {api.roles.map((r) => <option key={r.id} value={r.id}>{r.title}{r.employer ? ` — ${r.employer}` : ""}</option>)}
        </select>) },
    { key: "start", label: "Start (month)", htmlFor: `${id}-start`, view: friendlyMonth(p?.start_date, codes.includes("year_only_start")),
      edit: <MonthInput id={`${id}-start`} value={f.start} onChange={(v) => set({ start: v })} /> },
    { key: "end", label: "End (month)", htmlFor: `${id}-end`, view: friendlyMonth(p?.end_date, codes.includes("year_only_end")),
      edit: <MonthInput id={`${id}-end`} value={f.end} onChange={(v) => set({ end: v })} /> },
    { key: "description", label: "Description", htmlFor: `${id}-desc`, wide: true, view: p?.description,
      edit: <Textarea id={`${id}-desc`} rows={2} value={f.description} onChange={(e) => set({ description: e.target.value })} /> },
    { key: "bullets", label: "Bullets", wide: true, view: p?.bullets.length ? <BulletList items={p.bullets} /> : null,
      edit: <BulletEditor id={id} bullets={f.bullets} onChange={(b) => set({ bullets: b })} /> },
  ];

  async function save() {
    setSaving(true);
    const out = await api.apply({ op: p ? "edit" : "add", item: "project", id: p?.id, data: {
      kind: f.kind, name: f.name, url: f.url, description: f.description, start_date: f.start || null,
      end_date: f.end || null, role_id: f.role_id || null, bullets: texts(f.bullets), details: p?.details } },
    p ? "Project saved" : "Project added");
    setSaving(false);
    if (out) onDone?.();
    return !!out;
  }

  return (
    <EntryCard id={id} kindLabel="Project" testId="project-card" title={p?.name || "New project"}
      dates={p ? friendlyRange(p.start_date, p.end_date, false, codes) : undefined}
      meta={p ? [fork && "fork without your own commits (not counted)", plural(p.bullets.length, "bullet")].filter(Boolean).join(" · ") : undefined}
      sources={p?.sources ?? (github ? ["GitHub"] : [])} checkDates={!!p && (p.year_only || p.review_reasons.length > 0)}
      edited={p?.source === "user_edit" && (p.sources ?? []).some((s) => s !== "Added by you")}
      notes={p && <DateNotes item={p} kind="project" onEdit={async () => { if (await startEdit(id)) reset(); }} />}
      fields={fields} quote={p?.quote} saving={saving} canSave={!!f.name.trim()}
      onEditStart={reset} onSave={save} onCancel={() => { reset(); onDone?.(); }}
      onDelete={p ? () => api.remove({ op: "delete", item: "project", id: p.id }, p.name) : undefined} />
  );
}

// --- Certifications ------------------------------------------------------------------------------------------------------

type CertDraft = { name: string; issuer: string; issued: string; credential_id: string; verify_url: string };

export function CertificationCard({ cert: c, onDone }: { cert: Certification | null; onDone?: () => void }) {
  const api = useReviewApi();
  const id = c?.id ?? "new-certification";
  const init = (): CertDraft => ({ name: c?.name ?? "", issuer: c?.issuer ?? "", issued: monthValue(c?.issued_date),
    credential_id: c?.credential_id ?? "", verify_url: c?.verify_url ?? "" });
  const { draft: f, setDraft: setF, reset } = useDraft(id, init);
  const [saving, setSaving] = useState(false);
  const set = (x: Partial<CertDraft>) => setF({ ...f, ...x });
  const status = c?.credential_status ?? null;
  const fields: FieldSpec[] = [
    { key: "name", label: "Name", htmlFor: `${id}-name`, wide: true, view: c?.name,
      edit: <Input id={`${id}-name`} required value={f.name} placeholder="e.g. AWS Certified Machine Learning Engineer – Associate (MLA-C01)" onChange={(e) => set({ name: e.target.value })} /> },
    { key: "issuer", label: "Issuer", htmlFor: `${id}-issuer`, view: c?.issuer,
      edit: <Input id={`${id}-issuer`} value={f.issuer} placeholder="e.g. Amazon Web Services" onChange={(e) => set({ issuer: e.target.value })} /> },
    { key: "issued", label: "Issued (month)", htmlFor: `${id}-issued`, view: friendlyMonth(c?.issued_date),
      edit: <MonthInput id={`${id}-issued`} value={f.issued} onChange={(v) => set({ issued: v })} /> },
    { key: "credential_id", label: "Credential ID (optional)", htmlFor: `${id}-cred`, view: c?.credential_id,
      edit: <Input id={`${id}-cred`} value={f.credential_id} onChange={(e) => set({ credential_id: e.target.value })} /> },
    { key: "verify_url", label: "Link (optional)", htmlFor: `${id}-link`, view: c?.verify_url ? <Link url={c.verify_url} /> : null,
      edit: <Input id={`${id}-link`} value={f.verify_url} placeholder="https://" onChange={(e) => set({ verify_url: e.target.value })} /> },
  ];
  async function save() {
    setSaving(true);
    const out = await api.apply({ op: c ? "edit" : "add", item: "certification", id: c?.id, data: {
      name: f.name, issuer: f.issuer, issued_date: f.issued || null, credential_id: f.credential_id,
      verify_url: f.verify_url } }, c ? "Certification saved" : "Certification added");
    setSaving(false);
    if (out) onDone?.();
    return !!out;
  }
  return (
    <EntryCard id={id} kindLabel="Certification" testId="certification-card" title={c?.name || "New certification"}
      org={c?.issuer} dates={c?.issued_date ? friendlyMonth(c.issued_date) : undefined}
      meta={c ? (status ?? "Add an ID or link (optional)") : undefined} sources={c?.sources}
      fields={fields} saving={saving} canSave={!!f.name.trim()} defaultOpen={!c}
      onEditStart={reset} onSave={save} onCancel={() => { reset(); onDone?.(); }}
      onDelete={c ? () => api.remove({ op: "delete", item: "certification", id: c.id }, c.name) : undefined} />
  );
}

/** "+ Add …": a new card opened straight in edit mode. */
export function AddButton({ id, label, render }: { id: string; label: string;
  render: (done: () => void) => React.ReactNode }) {
  const { editing, startEdit } = useReviewEdit();
  const [open, setOpen] = useState(false);
  if (open && editing === id) return <>{render(() => setOpen(false))}</>;
  return (
    <Button type="button" variant="outline" size="sm" onClick={async () => { if (await startEdit(id)) setOpen(true); }}>
      + {label}
    </Button>
  );
}
