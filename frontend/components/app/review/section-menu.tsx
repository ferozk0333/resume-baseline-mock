"use client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type MenuEntry = { id: string; label: string; count?: number; note?: string; disabled?: boolean };

/** Sticky section menu (specs/19 item 11): jumps to a section and highlights the one in view; a dropdown on mobile. */
export function SectionMenu({ entries, variant = "side" }: { entries: MenuEntry[]; variant?: "side" | "bar" }) {
  const [current, setCurrent] = useState(entries[0]?.id);
  useEffect(() => {                                 // the current section: the last one whose top has passed 40%
    const ids = entries.filter((e) => !e.disabled).map((e) => e.id);
    const update = () => {
      const line = window.innerHeight * 0.4;
      let cur = ids[0];
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) cur = id;
      }
      setCurrent(cur);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => { window.removeEventListener("scroll", update); window.removeEventListener("resize", update); };
  }, [entries]);

  function go(id: string) {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    setCurrent(id);
    el.focus({ preventScroll: true });
  }
  const text = (e: MenuEntry) => `${e.label}${e.count !== undefined ? ` (${e.count})` : ""}`;

  if (variant === "bar") {                          // mobile: a dropdown in the sticky Confirm bar
    return (
      <label className="min-w-0 flex-1 lg:hidden" data-testid="section-menu-mobile">
        <span className="sr-only">Jump to section</span>
        <select value={current} onChange={(e) => go(e.target.value)} aria-label="Jump to section"
          className="h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm font-medium">
          {entries.map((e) => <option key={e.id} value={e.id} disabled={e.disabled}>{text(e)}{e.note ? ` · ${e.note}` : ""}</option>)}
        </select>
      </label>
    );
  }
  return (
    <nav aria-label="Review sections" data-testid="section-menu" className="sticky top-[calc(var(--header-h)+1rem)] hidden lg:block">
      <ul className="space-y-0.5">
        {entries.map((e) => (
          <li key={e.id}>
            {e.disabled ? (
              <span className="block rounded-md px-2.5 py-1.5 text-sm text-muted-foreground">{e.label}{e.note && <span className="block text-xs">{e.note}</span>}</span>
            ) : (
              <a href={`#${e.id}`} aria-current={current === e.id ? "location" : undefined}
                onClick={(ev) => { ev.preventDefault(); go(e.id); }}
                className={cn("flex items-center justify-between rounded-md px-2.5 py-1.5 text-sm hover:bg-muted",
                  current === e.id ? "bg-primary/10 font-medium text-primary" : "text-foreground")}>
                <span>{e.label}</span>
                {e.count !== undefined && <span className="text-xs text-muted-foreground tabular-nums">{e.count}</span>}
              </a>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
