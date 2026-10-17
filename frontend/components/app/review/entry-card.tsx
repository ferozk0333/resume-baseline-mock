"use client";
import { useState } from "react";
import { AlertTriangle, ChevronDown, Loader2, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ReadOnlyField } from "@/components/ui/read-only-field";
import { cn } from "@/lib/utils";
import { useReviewEdit } from "./edit-context";

export type FieldSpec = {
  key: string; label: string; wide?: boolean; hidden?: boolean;
  view: React.ReactNode;
  /** The control shown in edit mode, in the same place. `htmlFor` is the control's id (for the label). */
  edit: React.ReactNode; htmlFor?: string;
};

const SOURCE_CLASS: Record<string, string> = {
  Resume: "border-sky-200 bg-sky-50 text-sky-900", LinkedIn: "border-blue-200 bg-blue-50 text-blue-900",
  GitHub: "border-zinc-300 bg-zinc-50 text-zinc-900", Portfolio: "border-violet-200 bg-violet-50 text-violet-900",
  "Added by you": "border-emerald-200 bg-emerald-50 text-emerald-900",
};

export function SourceChips({ sources }: { sources: string[] }) {
  if (!sources.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1" aria-label={`From ${sources.join(", ")}`} data-testid="source-chips">
      {sources.map((s) => (
        <span key={s} className={cn("rounded-full border px-1.5 py-px text-[11px] font-medium leading-4", SOURCE_CLASS[s] ?? "border-border")}>{s}</span>
      ))}
    </span>
  );
}

/** The one entry card (specs/19 item 7) for roles, projects, education and every proof type: a fixed field grid that
 * is the same in view and edit mode. "Edit" turns the fields into inputs in place; Save / Cancel; one card at a time. */
export function EntryCard({ id, title, org, dates, meta, sources = [], checkDates, edited, notes, fields, quote,
  onEditStart, onSave, onCancel, onDelete, saving, canSave = true, defaultOpen = true, testId, kindLabel }: {
  id: string; title: string; org?: string | null; dates?: string; meta?: string; sources?: string[];
  checkDates?: boolean; edited?: boolean; notes?: React.ReactNode; fields: FieldSpec[]; quote?: string | null;
  onEditStart: () => void; onSave: () => Promise<boolean>; onCancel: () => void; onDelete?: () => void;
  saving?: boolean; canSave?: boolean; defaultOpen?: boolean; testId?: string; kindLabel: string;
}) {
  const { editing, startEdit, stopEdit } = useReviewEdit();
  const isEditing = editing === id;
  const [open, setOpen] = useState(defaultOpen);
  const shown = open || isEditing;
  const heading = org ? `${title} — ${org}` : title;

  async function edit() {
    if (await startEdit(id)) { onEditStart(); setOpen(true); }
  }
  async function cancel() {
    if (await stopEdit(id, { discard: true })) onCancel();
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (await onSave()) await stopEdit(id);
  }

  return (
    <article id={`card-${id}`} tabIndex={-1} data-testid={testId ?? "entry-card"} data-editing={isEditing || undefined}
      aria-label={`${kindLabel}: ${heading}`}
      className={cn("scroll-mt-[calc(var(--header-h)+1rem)] rounded-lg border bg-card outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-ring",
        isEditing && "border-primary ring-2 ring-primary/20", checkDates && !isEditing && "border-amber-300")}>
      <header className="flex items-start gap-2 px-3 py-2.5">
        <button type="button" className="flex min-w-0 flex-1 items-start gap-2 rounded text-left" aria-expanded={shown}
          onClick={() => setOpen(!open)} disabled={isEditing}>
          <ChevronDown className={cn("mt-0.5 size-4 shrink-0 transition-transform", !shown && "-rotate-90")} aria-hidden />
          <span className="min-w-0 space-y-1">
            <span className="block text-sm font-semibold break-words">{heading}</span>
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              {dates && <span>{dates}</span>}
              {meta && <span>{meta}</span>}
              <SourceChips sources={sources} />
              {edited && <Badge variant="outline" className="h-4 px-1.5 text-[11px]">Edited</Badge>}
              {checkDates && <Badge className="h-4 bg-amber-100 px-1.5 text-[11px] text-amber-900"><AlertTriangle /> Check dates</Badge>}
            </span>
          </span>
        </button>
        {!isEditing && (
          <div className="flex shrink-0 items-center gap-0.5">
            <Button type="button" variant="outline" size="sm" onClick={edit} aria-label={`Edit ${heading}`}>
              <Pencil /> Edit
            </Button>
            {onDelete && (
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${heading}`} onClick={onDelete}>
                <Trash2 />
              </Button>
            )}
          </div>
        )}
      </header>
      {shown && (
        <form className="border-t px-3 py-3" onSubmit={save} aria-label={isEditing ? `Edit ${heading}` : undefined}>
          {notes && !isEditing && <div className="mb-3 space-y-2">{notes}</div>}
          <div className="grid gap-3 sm:grid-cols-2">
            {fields.filter((f) => !f.hidden).map((f) => (
              <div key={f.key} className={cn("min-w-0", f.wide && "sm:col-span-2")}>
                {isEditing ? (
                  f.htmlFor ? (
                    <div className="space-y-1.5"><Label htmlFor={f.htmlFor}>{f.label}</Label>{f.edit}</div>
                  ) : f.edit
                ) : (
                  <ReadOnlyField label={f.label}>{f.view}</ReadOnlyField>
                )}
              </div>
            ))}
          </div>
          {quote && !isEditing && (
            <p className="mt-3 border-l-2 pl-2 text-xs text-muted-foreground">Source: “{quote}”</p>
          )}
          {isEditing && (
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
              <Button type="submit" disabled={!canSave || saving}>{saving && <Loader2 className="animate-spin" />} Save</Button>
              <Button type="button" variant="outline" onClick={cancel}>Cancel</Button>
            </div>
          )}
        </form>
      )}
    </article>
  );
}
