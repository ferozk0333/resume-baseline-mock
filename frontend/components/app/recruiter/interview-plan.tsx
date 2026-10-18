"use client";
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { del, get, patch, post, put, type JobSettings, type Stage } from "@/lib/api";

/** Interview plan per job (specs/13 §3): add, rename, reorder (drag or arrows), delete, focus areas. */
export function InterviewPlan({ jobId }: { jobId: string }) {
  const [stages, setStages] = useState<Stage[]>([]);
  const [settings, setSettings] = useState<JobSettings | null>(null);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState<string | null>(null);

  useEffect(() => {
    get<{ settings: JobSettings; stages: Stage[] }>(`/jobs/${jobId}/settings`).then((d) => {
      setStages(d.stages); setSettings(d.settings);
    }).catch((e) => toast.error(e.message));
  }, [jobId]);

  async function run(p: Promise<Stage[]>) {
    try { setStages(await p); } catch (e) { toast.error((e as Error).message); }
  }
  const movable = stages.filter((s) => !s.terminal);
  const terminal = stages.filter((s) => s.terminal);
  function reorder(from: string, to: string) {
    const ids = movable.map((s) => s.id);
    ids.splice(ids.indexOf(to), 0, ids.splice(ids.indexOf(from), 1)[0]);
    run(put<Stage[]>(`/jobs/${jobId}/stages/order`, { ids }));
  }
  function shift(i: number, by: number) {
    const ids = movable.map((s) => s.id);
    const j = i + by;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    run(put<Stage[]>(`/jobs/${jobId}/stages/order`, { ids }));
  }
  async function saveSettings() {
    try {
      const d = await put<{ settings: JobSettings }>(`/jobs/${jobId}/settings`, settings);
      setSettings(d.settings); toast.success("Scheduling settings saved");
    } catch (e) { toast.error((e as Error).message); }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Interview plan</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <ol className="space-y-1.5">
            {movable.map((s, i) => (
              <li key={s.id} draggable onDragStart={() => setDragging(s.id)} onDragEnd={() => setDragging(null)}
                onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragging && dragging !== s.id) reorder(dragging, s.id); }}
                className="flex items-center gap-2 rounded-lg border bg-white p-2">
                <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" aria-hidden />
                <span className="w-5 text-xs tabular-nums text-muted-foreground">{i + 1}</span>
                <Input aria-label={`Stage ${i + 1} name`} defaultValue={s.name} className="h-8 w-48 bg-white"
                  onBlur={(e) => e.target.value !== s.name && run(patch<Stage[]>(`/jobs/${jobId}/stages/${s.id}`, { name: e.target.value }))} />
                <Input aria-label={`${s.name} focus areas`} placeholder="Focus areas, comma-separated" defaultValue={s.focus.join(", ")}
                  className="h-8 flex-1 bg-white text-xs"
                  onBlur={(e) => {
                    const focus = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
                    if (focus.join("|") !== s.focus.join("|")) run(patch<Stage[]>(`/jobs/${jobId}/stages/${s.id}`, { focus }));
                  }} />
                {s.applicants > 0 && <Badge variant="secondary" className="tabular-nums">{s.applicants}</Badge>}
                <Button size="icon-sm" variant="ghost" aria-label={`Move ${s.name} up`} disabled={i === 0} onClick={() => shift(i, -1)}><ArrowUp /></Button>
                <Button size="icon-sm" variant="ghost" aria-label={`Move ${s.name} down`} disabled={i === movable.length - 1} onClick={() => shift(i, 1)}><ArrowDown /></Button>
                <Button size="icon-sm" variant="ghost" aria-label={`Delete ${s.name}`}
                  onClick={() => run(del(`/jobs/${jobId}/stages/${s.id}`).then(() => get<{ stages: Stage[] }>(`/jobs/${jobId}/settings`)).then((d) => d.stages))}>
                  <Trash2 />
                </Button>
              </li>
            ))}
            {terminal.map((s) => (
              <li key={s.id} className="flex items-center gap-2 rounded-lg border border-dashed p-2 text-sm text-muted-foreground">
                <span className="w-9" />{s.name}<span className="text-xs">· terminal stage (fixed)</span>
              </li>
            ))}
          </ol>
          <form className="flex gap-2 pt-1" onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) run(post<Stage[]>(`/jobs/${jobId}/stages`, { name }).then((r) => { setName(""); return r; }));
          }}>
            <Input aria-label="New stage name" placeholder="New stage, e.g. Take-home task" value={name}
              onChange={(e) => setName(e.target.value)} className="h-8 w-64 bg-white" />
            <Button size="sm" variant="outline" type="submit"><Plus /> Add stage</Button>
          </form>
        </CardContent>
      </Card>

      {settings && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Scheduling</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1"><Label htmlFor="rname">Recruiter name</Label>
              <Input id="rname" value={settings.recruiter_name} onChange={(e) => setSettings({ ...settings, recruiter_name: e.target.value })} className="bg-white" /></div>
            <div className="space-y-1"><Label htmlFor="dur">Interview duration (minutes)</Label>
              <Input id="dur" type="number" min={5} max={240} value={settings.duration_minutes}
                onChange={(e) => setSettings({ ...settings, duration_minutes: Number(e.target.value) })} className="bg-white" /></div>
            <div className="space-y-1"><Label htmlFor="avail">Availability windows</Label>
              <Input id="avail" value={settings.availability} placeholder="Mon–Wed 10:00–14:00 ET"
                onChange={(e) => setSettings({ ...settings, availability: e.target.value })} className="bg-white" /></div>
            <div className="space-y-1"><Label htmlFor="link">Scheduling link (optional, replaces the windows)</Label>
              <Input id="link" value={settings.scheduling_link} placeholder="https://…"
                onChange={(e) => setSettings({ ...settings, scheduling_link: e.target.value })} className="bg-white" /></div>
            <div><Button size="sm" onClick={saveSettings}>Save scheduling settings</Button></div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
