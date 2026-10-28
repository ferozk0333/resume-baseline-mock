"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { get } from "@/lib/api";
import { ExtLink } from "@/components/shared/ext-link";

type Flag = { id: string; evidence_id: string | null; source: string; chunk_index: number; chunk: string; score: number;
  hidden: number; model: string; created_at: string };
type Overview = { candidates: { id: string; name: string | null; parser_runs: number; flagged: number; hidden_docs: number }[];
  market_flags: Flag[]; enabled: { prompt_guard: boolean; remove_hidden_text: boolean }; threshold: number; model_doc: string };
type Detail = { documents: { evidence_id: string; type: string; status: string; hidden_text: { page: number; text: string; reason: string }[] }[];
  parser_runs: { id: string; doc_type: string; mode: string; outcome: string; reasons_json: string[] | null; seconds: number;
    jev_requests: number; jev_questions: number; llm_calls: number; created_at: string }[];
  flagged_chunks: Flag[] };

/** Admin → Screening (specs/18 Phase 4): hidden text removed before reading, how each document was read, and chunks the
 * prompt-injection screen flagged. Admin only: never shown to recruiters, never used in a score. */
export default function Screening() {
  const [data, setData] = useState<Overview | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ id: string; data: Detail } | null>(null);
  const detail = loaded && loaded.id === open ? loaded.data : null;
  useEffect(() => { get<Overview>("/admin/screening").then(setData).catch((e) => toast.error(e.message)); }, []);
  useEffect(() => {
    if (!open) return;
    get<Detail>(`/admin/candidates/${open}/extraction`).then((d) => setLoaded({ id: open, data: d }))
      .catch((e) => toast.error(e.message));
  }, [open]);
  if (!data) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Screening</h1>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          <Badge variant={data.enabled.remove_hidden_text ? "default" : "outline"}>Hidden-text removal {data.enabled.remove_hidden_text ? "on" : "off"}</Badge>
          <Badge variant={data.enabled.prompt_guard ? "default" : "outline"}>Injection screen {data.enabled.prompt_guard ? "on" : "off"}</Badge>
          <ExtLink url={data.model_doc}>Screen model (512-token window)</ExtLink>
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <Card className="h-fit">
          <CardHeader><CardTitle className="text-base">Candidates</CardTitle></CardHeader>
          <CardContent>
            {data.candidates.length === 0 && <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>}
            <ul className="space-y-1">
              {data.candidates.map((c) => (
                <li key={c.id}>
                  <button type="button" onClick={() => setOpen(c.id)} aria-pressed={open === c.id}
                    className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted aria-pressed:bg-muted">
                    <span className="font-medium">{c.name ?? c.id.slice(0, 8)}</span>
                    <span className="block text-xs text-muted-foreground">
                      {c.parser_runs} parser log · {c.hidden_docs} with hidden text · {c.flagged} flagged
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <div className="space-y-4">
          {!open && <p className="text-sm text-muted-foreground">Pick a candidate.</p>}
          {open && !detail && <Skeleton className="h-64" />}
          {detail && (
            <>
              <Card>
                <CardHeader><CardTitle className="text-base">Hidden text removed</CardTitle>
                  </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {detail.documents.every((d) => d.hidden_text.length === 0) && <p className="text-muted-foreground">None.</p>}
                  {detail.documents.filter((d) => d.hidden_text.length).map((d) => (
                    <div key={d.evidence_id}><span className="font-medium">{d.type}</span>
                      <ul className="list-disc pl-5">{d.hidden_text.map((h, i) => <li key={i}>{h.text} <span className="text-xs text-muted-foreground">({h.reason})</span></li>)}</ul>
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-base">Parser log</CardTitle></CardHeader>
                <CardContent className="overflow-x-auto">
                  {detail.parser_runs.length === 0 ? <p className="text-sm text-muted-foreground">No parser runs.</p> : (
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs text-muted-foreground"><tr><th className="py-1">Document</th><th>Mode</th><th>Outcome</th><th>Why</th><th>Label requests</th><th>Model reads</th><th>Time</th></tr></thead>
                      <tbody>{detail.parser_runs.map((r) => (
                        <tr key={r.id} className="border-t align-top"><td className="py-1">{r.doc_type}</td><td>{r.mode}</td><td>{r.outcome}</td>
                          <td className="max-w-xs">{(r.reasons_json ?? []).join("; ")}</td><td>{r.jev_requests}/{r.jev_questions}</td><td>{r.llm_calls}</td>
                          <td>{(r.seconds * 1000).toFixed(0)} ms</td></tr>))}</tbody>
                    </table>)}
                </CardContent>
              </Card>
              <FlagList title="Flagged chunks" flags={detail.flagged_chunks} />
            </>
          )}
        </div>
      </div>
      <FlagList title="Flagged market postings" flags={data.market_flags} />
    </div>
  );
}

function FlagList({ title, flags }: { title: string; flags: Flag[] }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-sm">
        {flags.length === 0 && <p className="text-muted-foreground">None.</p>}
        {flags.map((f) => (
          <div key={f.id} className="rounded-md border p-2">
            <div className="mb-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
              <span>{f.source} · chunk {f.chunk_index + 1}</span><span>score {f.score.toFixed(2)}</span>
              {f.hidden ? <Badge variant="outline">hidden text</Badge> : null}
            </div>
            <p className="whitespace-pre-wrap break-words">{f.chunk}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
