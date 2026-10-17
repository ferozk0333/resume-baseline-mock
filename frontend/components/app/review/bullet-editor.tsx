"use client";
import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type DraftBullet = { key: string; text: string };

let seq = 0;
export const newBullet = (text = ""): DraftBullet => ({ key: `b${++seq}`, text });

/** Bullets edited one by one (specs/19 item 8): each its own field, delete, reorder by drag or with the up/down
 * buttons, "+ Add bullet", and pasting several lines into a bullet splits them. Empty bullets are not saved. */
export function BulletEditor({ id, bullets, onChange, label = "Bullets" }: {
  id: string; bullets: DraftBullet[]; onChange: (b: DraftBullet[]) => void; label?: string;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const refs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const [status, setStatus] = useState("");

  const focus = (key: string) => setTimeout(() => refs.current[key]?.focus(), 0);
  const set = (i: number, text: string) => onChange(bullets.map((b, j) => (j === i ? { ...b, text } : b)));
  function move(from: number, to: number, announce = true) {
    if (to < 0 || to >= bullets.length || from === to) return;
    const next = [...bullets];
    const [b] = next.splice(from, 1);
    next.splice(to, 0, b);
    onChange(next);
    if (announce) setStatus(`Bullet moved to position ${to + 1} of ${bullets.length}.`);
  }
  function remove(i: number) {
    const next = bullets.filter((_, j) => j !== i);
    onChange(next);
    setStatus("Bullet deleted.");
    if (next.length) focus(next[Math.min(i, next.length - 1)].key);
  }
  function add() {
    const b = newBullet();
    onChange([...bullets, b]);
    focus(b.key);
  }
  function paste(i: number, e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const text = e.clipboardData.getData("text");
    const lines = text.split(/\r?\n/).map((l) => l.replace(/^\s*(?:[-*•▪●◦‣]|\d+[.)])\s+/, "").trim()).filter(Boolean);
    if (lines.length < 2 || bullets[i].text.trim()) return;   // only an empty bullet splits a multi-line paste
    e.preventDefault();
    const made = lines.map((l) => newBullet(l));
    onChange([...bullets.slice(0, i), ...made, ...bullets.slice(i + 1)]);
    setStatus(`Pasted ${lines.length} bullets.`);
    focus(made[made.length - 1].key);
  }

  return (
    <fieldset className="space-y-2" data-testid={`bullets-${id}`}>
      <legend className="mb-1.5 text-sm font-medium">{label}</legend>
      <ol className="space-y-2">
        {bullets.map((b, i) => (
          <li key={b.key} data-testid="bullet-row"
            onDragOver={(e) => { if (drag !== null) { e.preventDefault(); setOver(i); } }}
            onDrop={(e) => { e.preventDefault(); if (drag !== null) move(drag, i); setDrag(null); setOver(null); }}
            className={cn("flex items-start gap-1.5 rounded-lg", over === i && drag !== null && drag !== i && "ring-2 ring-primary/40")}>
            <span draggable aria-hidden title="Drag to reorder" data-testid="drag-handle"
              onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); }}
              onDragEnd={() => { setDrag(null); setOver(null); }}
              className="mt-1.5 cursor-grab rounded p-0.5 text-muted-foreground hover:bg-muted active:cursor-grabbing">
              <GripVertical className="size-4" />
            </span>
            <Textarea ref={(el) => { refs.current[b.key] = el; }} rows={1} value={b.text}
              aria-label={`Bullet ${i + 1} of ${bullets.length}`} placeholder="What you did and what changed because of it"
              className="min-h-8 flex-1 py-1.5" onChange={(e) => set(i, e.target.value)} onPaste={(e) => paste(i, e)}
              onKeyDown={(e) => {
                if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); move(i, i - 1); focus(b.key); }
                if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); move(i, i + 1); focus(b.key); }
              }} />
            <div className="flex shrink-0 items-center">
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move bullet ${i + 1} up`} disabled={i === 0}
                onClick={() => move(i, i - 1)}><ArrowUp /></Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move bullet ${i + 1} down`}
                disabled={i === bullets.length - 1} onClick={() => move(i, i + 1)}><ArrowDown /></Button>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete bullet ${i + 1}`}
                onClick={() => remove(i)}><Trash2 /></Button>
            </div>
          </li>
        ))}
      </ol>
      <Button type="button" variant="outline" size="sm" onClick={add}><Plus /> Add bullet</Button>
      <p className="text-xs text-muted-foreground">Tip: paste several lines into an empty bullet to split them. Alt + ↑/↓ moves a bullet.</p>
      <p className="sr-only" role="status" aria-live="polite">{status}</p>
    </fieldset>
  );
}
