"use client";
import { useMemo, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Claim } from "@/lib/api";
import { useReviewApi } from "./review-api";

const FIRST = 12;

/** Skills and tools, grouped under reviewed headings (specs/19 item 10). Names only: the skill/tool distinction stays
 * in the data and changes no score. Search filters every group; remove has "Undo". */
export function Skills({ claims, groups }: { claims: Claim[]; groups: string[] }) {
  const api = useReviewApi();
  const [q, setQ] = useState("");
  const [all, setAll] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [group, setGroup] = useState("");
  const query = q.trim().toLowerCase();
  const byGroup = useMemo(() => {
    const out = new Map<string, Claim[]>(groups.map((g) => [g, []]));
    for (const c of claims) {
      if (query && !c.name_raw.toLowerCase().includes(query)) continue;
      const g = out.has(c.group ?? "") ? c.group! : "Other";
      if (!out.has(g)) out.set(g, []);
      out.get(g)!.push(c);
    }
    return [...out.entries()].filter(([, cs]) => cs.length > 0);
  }, [claims, groups, query]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    if (await api.apply({ op: "add", item: "claim", data: { kind: "skill", name: name.trim(), group: group || null } },
      `${name.trim()} added`)) setName("");
  }

  return (
    <div className="space-y-4">
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden />
        <Input type="search" aria-label="Search skills and tools" placeholder={`Search ${claims.length} skills and tools`}
          value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
      </div>
      {byGroup.length === 0 && <p className="text-sm text-muted-foreground">{query ? `No skills match “${q}”.` : "None yet."}</p>}
      {byGroup.map(([g, cs]) => {
        const open = all[g] || !!query;
        const shown = open ? cs : cs.slice(0, FIRST);
        return (
          <section key={g} aria-label={g} data-testid={`skill-group-${g}`}>
            <h4 className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{g} <span className="font-normal">({cs.length})</span></h4>
            <ul className="flex flex-wrap gap-1.5">
              {shown.map((c) => (
                <li key={c.id} className="inline-flex items-center gap-1 rounded-full border bg-card py-0.5 pr-0.5 pl-2.5 text-sm" data-testid="skill-chip">
                  {c.name_raw}
                  {c.bullet_id && <span className="size-1.5 rounded-full bg-emerald-600" title="Named in one of your bullets"><span className="sr-only">(named in a bullet)</span></span>}
                  <button type="button" aria-label={`Remove ${c.name_raw}`} className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={() => api.remove({ op: "delete", item: "claim", id: c.id }, c.name_raw, false)}>
                    <X className="size-3" />
                  </button>
                </li>
              ))}
            </ul>
            {!open && cs.length > FIRST && (
              <Button type="button" variant="link" size="xs" className="mt-1 px-0" onClick={() => setAll({ ...all, [g]: true })}>
                Show all ({cs.length})
              </Button>
            )}
          </section>
        );
      })}
      <form onSubmit={add} className="flex flex-wrap items-end gap-2 border-t pt-3" aria-label="Add a skill">
        <div className="min-w-40 flex-1 space-y-1.5">
          <Label htmlFor="add-skill-name">Skill or tool</Label>
          <Input id="add-skill-name" placeholder="e.g. SQL" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="add-skill-group">Group</Label>
          <select id="add-skill-group" value={group} onChange={(e) => setGroup(e.target.value)}
            className="h-8 rounded-lg border border-field-border bg-field px-2 text-sm">
            <option value="">Choose for me</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </div>
        <Button type="submit" variant="outline" disabled={!name.trim()}><Plus /> Add skill</Button>
      </form>
    </div>
  );
}
