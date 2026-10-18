"use client";
import { useState } from "react";
import { Info, Plus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Claim, ExtraItem, Project } from "@/lib/api";
import { BulletList, Link, plural, useDraft } from "./cards";
import { friendlyMonth, monthValue } from "./dates";
import { useReviewEdit } from "./edit-context";
import { EntryCard, type FieldSpec } from "./entry-card";
import { useReviewApi, type Edit } from "./review-api";

type FieldKind = "text" | "url" | "month" | "year" | "textarea";
type ProofField = { key: string; label: string; kind?: FieldKind; placeholder?: string; wide?: boolean; required?: boolean };

/** One "More proof" type (specs/19 Phase D). `store` is where it lives: a project of an existing evidence kind, an award
 * claim, or a display-only item (never judged or scored). */
export type ProofType = {
  key: string; label: string; one: string; store: "project" | "claim" | "extra"; kind?: string; scored: boolean;
  fields: ProofField[]; title: string; org?: string; date?: string;
};

export const PROOF_TYPES: ProofType[] = [
  { key: "publication", label: "Publications", one: "publication", store: "project", kind: "publication", scored: true, title: "title", org: "venue", date: "year",
    fields: [{ key: "title", label: "Title", required: true, wide: true }, { key: "venue", label: "Venue", placeholder: "Journal, conference or site" },
      { key: "year", label: "Year", kind: "year" }, { key: "link", label: "Link", kind: "url" }, { key: "role", label: "Your role", placeholder: "e.g. First author" }] },
  { key: "presentation", label: "Talks & conferences", one: "talk", store: "project", kind: "presentation", scored: true, title: "title", org: "event", date: "date",
    fields: [{ key: "title", label: "Title", required: true, wide: true }, { key: "event", label: "Event" },
      { key: "date", label: "Date (month)", kind: "month" }, { key: "link", label: "Link", kind: "url" }] },
  { key: "award", label: "Awards", one: "award", store: "claim", scored: true, title: "name", org: "issuer", date: "date",
    fields: [{ key: "name", label: "Name", required: true, wide: true }, { key: "issuer", label: "Issuer" },
      { key: "date", label: "Date (month)", kind: "month" }, { key: "for", label: "What it was for", kind: "textarea", wide: true }] },
  { key: "hackathon", label: "Hackathons & competitions", one: "hackathon or competition", store: "project", kind: "hackathon", scored: true, title: "event", org: "placement", date: "date",
    fields: [{ key: "event", label: "Event", required: true }, { key: "date", label: "Date (month)", kind: "month" },
      { key: "placement", label: "Placement", placeholder: "e.g. Top 10 of 600 teams" }, { key: "link", label: "Project link", kind: "url" }] },
  { key: "open_source", label: "Open-source contributions", one: "open-source contribution", store: "project", kind: "project", scored: true, title: "project",
    fields: [{ key: "project", label: "Project", required: true }, { key: "link", label: "Link", kind: "url" },
      { key: "contributed", label: "What you contributed", kind: "textarea", wide: true }] },
  { key: "case_study", label: "Case studies", one: "case study", store: "project", kind: "case_study", scored: true, title: "title",
    fields: [{ key: "title", label: "Title", required: true, wide: true }, { key: "link", label: "Link", kind: "url" },
      { key: "summary", label: "Summary", kind: "textarea", wide: true }] },
  { key: "patent", label: "Patents", one: "patent", store: "extra", scored: false, title: "title", org: "number", date: "date",
    fields: [{ key: "title", label: "Title", required: true, wide: true }, { key: "number", label: "Number" },
      { key: "date", label: "Date (month)", kind: "month" }, { key: "link", label: "Link", kind: "url" }] },
  { key: "volunteering", label: "Volunteering", one: "volunteering", store: "extra", scored: false, title: "organization", org: "role",
    fields: [{ key: "organization", label: "Organization", required: true }, { key: "role", label: "Role" },
      { key: "start", label: "Start (month)", kind: "month" }, { key: "end", label: "End (month)", kind: "month" },
      { key: "description", label: "Description", kind: "textarea", wide: true }] },
  { key: "course", label: "Courses", one: "course", store: "extra", scored: false, title: "name", org: "provider", date: "date",
    fields: [{ key: "name", label: "Name", required: true }, { key: "provider", label: "Provider" },
      { key: "date", label: "Date (month)", kind: "month" }, { key: "link", label: "Link", kind: "url" }] },
  { key: "language", label: "Languages", one: "language", store: "extra", scored: false, title: "language", org: "proficiency",
    fields: [{ key: "language", label: "Language", required: true }, { key: "proficiency", label: "Proficiency", placeholder: "e.g. Native, Fluent, Conversational" }] },
];

export const EDUCATION: ProofType = { key: "education", label: "Education", one: "education", store: "extra", scored: false,
  title: "school", org: "degree",
  fields: [{ key: "school", label: "School", required: true }, { key: "degree", label: "Degree" }, { key: "field", label: "Field" },
    { key: "start", label: "Start", kind: "month" }, { key: "end", label: "End", kind: "month" }, { key: "notes", label: "Notes", kind: "textarea", wide: true }] };

/** A More-proof item in one shape, whatever it is stored as. */
export type ProofItem = { id: string; values: Record<string, string>; sources: string[]; edited: boolean;
  quote?: string | null; project?: Project; claim?: Claim; extra?: ExtraItem };

const year = (d: string | null | undefined) => (d ? d.slice(0, 4) : "");

export function proofItems(t: ProofType, projects: Project[], claims: Claim[], extra: ExtraItem[]): ProofItem[] {
  if (t.store === "extra") {
    return extra.filter((x) => x.type === t.key).map((x) => ({ id: x.id, values: x.fields, sources: x.sources,
      edited: x.source === "user_edit" && x.sources.some((s) => s !== "Added by you"), quote: x.quote, extra: x }));
  }
  if (t.store === "claim") {
    return claims.filter((c) => c.kind === "award").map((c) => ({ id: c.id, sources: c.sources ?? [], quote: c.quote, claim: c,
      edited: c.source === "user_edit" && (c.sources ?? []).some((s) => s !== "Added by you"),
      values: { name: c.name_raw, issuer: c.details?.issuer ?? "", date: c.details?.date ?? "", for: c.details?.for ?? "" } }));
  }
  const mine = projects.filter((p) => t.key === "open_source" ? p.details?.proof_type === "open_source"
    : p.kind === t.kind && p.details?.proof_type !== "open_source");
  return mine.map((p) => {
    const v: Record<string, string> = { ...(p.details ?? {}), link: p.url ?? "" };
    v[t.title] = p.name;
    if (t.key === "publication") v.year = year(p.end_date);
    if (["presentation", "hackathon"].includes(t.key)) v.date = monthValue(p.end_date);
    if (t.key === "open_source") v.contributed = p.description ?? "";
    if (t.key === "case_study") v.summary = p.description ?? "";
    return { id: p.id, values: v, sources: p.sources ?? [], quote: p.quote, project: p,
      edited: p.source === "user_edit" && (p.sources ?? []).some((s) => s !== "Added by you") };
  });
}

function toEdit(t: ProofType, v: Record<string, string>, item: ProofItem | null): Edit {
  const op = item ? "edit" : "add";
  if (t.store === "extra") {
    return { op, item: "extra", id: item?.id, data: { type: t.key, fields: Object.fromEntries(t.fields.map((f) => [f.key, v[f.key] ?? ""])) } };
  }
  if (t.store === "claim") {
    return { op, item: "claim", id: item?.id, data: { kind: "award", name: v.name, details: { issuer: v.issuer, date: v.date, for: v.for } } };
  }
  const p = item?.project;
  const end = t.key === "publication" ? (v.year ? `${v.year}-12` : null) : (v.date || (p ? monthValue(p.end_date) : "") || null);
  const description = t.key === "open_source" ? v.contributed : t.key === "case_study" ? v.summary : p?.description ?? null;
  const details: Record<string, string> = { ...(p?.details ?? {}) };
  for (const f of t.fields) if (!["link", t.title, "year", "date", "contributed", "summary"].includes(f.key)) details[f.key] = v[f.key] ?? "";
  if (t.key === "open_source") details.proof_type = "open_source";
  return { op, item: "project", id: p?.id, data: { kind: p?.kind === "repo" ? "repo" : t.kind, name: v[t.title], url: v.link,
    description, start_date: p ? monthValue(p.start_date) || null : null, end_date: end, role_id: p?.role_id ?? null,
    bullets: (p?.bullets ?? []).map((b) => b.text), details } };
}

function view(f: ProofField, value: string | undefined) {
  if (!value) return null;
  if (f.kind === "url") return <Link url={value} />;
  if (f.kind === "month") return friendlyMonth(value);
  return value;
}

export function ProofCard({ t, item, onDone }: { t: ProofType; item: ProofItem | null; onDone?: () => void }) {
  const api = useReviewApi();
  const id = item?.id ?? `new-${t.key}`;
  const init = () => Object.fromEntries(t.fields.map((f) => [f.key, f.kind === "month" ? monthValue(item?.values[f.key]) : item?.values[f.key] ?? ""]));
  const { draft, setDraft, reset } = useDraft<Record<string, string>>(id, init);
  const [saving, setSaving] = useState(false);
  const required = t.fields.filter((f) => f.required).every((f) => (draft[f.key] ?? "").trim());
  const fields: FieldSpec[] = t.fields.map((f) => {
    const fid = `${id}-${f.key}`;
    const common = { id: fid, value: draft[f.key] ?? "", placeholder: f.placeholder,
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft({ ...draft, [f.key]: e.target.value }) };
    const edit = f.kind === "textarea" ? <Textarea rows={2} {...common} />
      : f.kind === "month" ? <Input type="month" {...common} />
      : f.kind === "year" ? <Input type="number" min={1950} max={2100} inputMode="numeric" {...common} />
      : <Input type={f.kind === "url" ? "url" : "text"} required={f.required} {...common} placeholder={f.placeholder ?? (f.kind === "url" ? "https://" : undefined)} />;
    return { key: f.key, label: f.label, wide: f.wide, htmlFor: fid, view: view(f, item?.values[f.key]), edit };
  });
  if (item?.project?.bullets.length) {
    fields.push({ key: "bullets", label: "Bullets", wide: true, view: <BulletList items={item.project.bullets} />,
      edit: <p className="text-xs text-muted-foreground">{plural(item.project.bullets.length, "bullet")} from your documents stay as they are.</p> });
  }
  async function save() {
    setSaving(true);
    const out = await api.apply(toEdit(t, draft, item), item ? "Saved" : `${t.one[0].toUpperCase()}${t.one.slice(1)} added`);
    setSaving(false);
    if (out) onDone?.();
    return !!out;
  }
  const v = item?.values ?? {};
  const dateField = t.fields.find((f) => f.key === t.date);
  const remove = !item ? undefined : () => api.remove(
    item.extra ? { op: "delete", item: "extra", id: item.id } : item.claim ? { op: "delete", item: "claim", id: item.id }
      : { op: "delete", item: "project", id: item.id }, v[t.title] || t.one);
  return (
    <EntryCard id={id} kindLabel={t.one} testId={`proof-card-${t.key}`} title={v[t.title] || `New ${t.one}`}
      org={t.org ? v[t.org] : undefined} dates={dateField && v[t.date!] ? (dateField.kind === "month" ? friendlyMonth(v[t.date!]) : v[t.date!]) : undefined}
      sources={item?.sources} edited={item?.edited} fields={fields} quote={item?.quote} saving={saving} canSave={required}
      defaultOpen={!item} onEditStart={reset} onSave={save} onCancel={() => { reset(); onDone?.(); }} onDelete={remove} />
  );
}

/** One proof type's cards and its "+ Add" (also used for Education). */
export function ProofSection({ t, items, adding, setAdding }: { t: ProofType; items: ProofItem[]; adding: boolean;
  setAdding: (b: boolean) => void }) {
  const { startEdit } = useReviewEdit();
  return (
    <section aria-labelledby={`proof-${t.key}`} data-testid={`proof-${t.key}`} className="space-y-2">
      <h3 id={`proof-${t.key}`} className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
        {t.label} <span className="font-normal text-muted-foreground">({items.length})</span>
        {!t.scored && <NotScored />}
      </h3>
      {items.map((x) => <ProofCard key={x.id} t={t} item={x} />)}
      {adding ? <ProofCard t={t} item={null} onDone={() => setAdding(false)} /> : (
        <button type="button" className="inline-flex h-7 items-center gap-1 rounded-md border border-dashed px-2.5 text-sm hover:bg-muted"
          onClick={async () => { if (await startEdit(`new-${t.key}`)) setAdding(true); }}>
          <Plus className="size-3.5" /> Add {t.one}
        </button>
      )}
    </section>
  );
}

export function NotScored() {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground" data-testid="not-scored">
      <Info className="size-3" aria-hidden /> Shown to recruiters, not scored
    </span>
  );
}

/** "More proof" (specs/19 item 20): one section per type that has items; empty types collapse into "+ Add …" chips. */
export function MoreProof({ projects, claims, extra }: { projects: Project[]; claims: Claim[]; extra: ExtraItem[] }) {
  const { startEdit } = useReviewEdit();
  const [adding, setAdding] = useState<string | null>(null);
  const rows = PROOF_TYPES.map((t) => ({ t, items: proofItems(t, projects, claims, extra) }));
  const open = rows.filter((r) => r.items.length > 0 || adding === r.t.key);
  const empty = rows.filter((r) => r.items.length === 0 && adding !== r.t.key);
  return (
    <div className="space-y-5">
      {open.length === 0 && <p className="text-sm text-muted-foreground">Nothing here yet.</p>}
      {open.map(({ t, items }) => (
        <ProofSection key={t.key} t={t} items={items} adding={adding === t.key} setAdding={(b) => setAdding(b ? t.key : null)} />
      ))}
      {empty.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t pt-3" data-testid="proof-add-chips" aria-label="Add more proof">
          {empty.map(({ t }) => (
            <button key={t.key} type="button" className="inline-flex h-7 items-center gap-1 rounded-full border border-dashed bg-card px-3 text-sm hover:bg-muted"
              onClick={async () => { if (await startEdit(`new-${t.key}`)) setAdding(t.key); }}>
              <Plus className="size-3.5" /> Add {t.one}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export const proofCount = (projects: Project[], claims: Claim[], extra: ExtraItem[]) =>
  PROOF_TYPES.reduce((n, t) => n + proofItems(t, projects, claims, extra).length, 0);
