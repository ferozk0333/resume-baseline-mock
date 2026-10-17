"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DIMENSIONS, get, titleCase, type CompareColumn } from "@/lib/api";

export default function ComparePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Compare /></Suspense>;
}

function Compare() {
  const { id } = useParams<{ id: string }>();
  const ids = useSearchParams().get("ids") ?? "";
  const [cols, setCols] = useState<CompareColumn[] | null>(null);
  useEffect(() => { get<CompareColumn[]>(`/jobs/${id}/compare?ids=${ids}`).then(setCols).catch((e) => toast.error(e.message)); }, [id, ids]);
  if (!cols) return <Skeleton className="h-96" />;
  const row = (label: string, render: (c: CompareColumn) => React.ReactNode) => (
    <div key={label} className="contents">
      <div className="py-2 text-sm text-muted-foreground">{label}</div>
      {cols.map((c) => <div key={c.application_id} className="py-2 text-sm tabular-nums">{render(c)}</div>)}
    </div>
  );
  const num = (v: number | null, f = (x: number) => x.toFixed(1)) => (v === null ? "—" : f(v));
  return (
    <div className="space-y-6">
      <div>
        <Link href={`/recruiter/jobs/${id}`} className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Applicants
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Compare applicants</h1>
      </div>
      <Card>
        <CardContent className="grid gap-x-6 divide-y" style={{ gridTemplateColumns: `10rem repeat(${cols.length}, minmax(0, 1fr))` }}>
          <div />
          {cols.map((c) => (
            <div key={c.application_id} className="pb-3">
              <div className="text-base font-semibold">{c.name}</div>
              {c.demo && <Badge variant="outline" className="mt-1 text-[11px] text-muted-foreground">demo</Badge>}
            </div>
          ))}
          {row("Match", (c) => <span className="text-lg font-semibold text-primary">{num(c.match)}</span>)}
          {row("Evidence confidence", (c) => num(c.confidence, (x) => String(Math.round(x))))}
          {DIMENSIONS.map((d) => row(titleCase(d), (c) => num(c.dimensions?.[d] ?? null, (x) => String(Math.round(x * 100)))))}
          {row("Must-haves", (c) => (c.scored ? `${c.must_haves_met} of ${c.must_haves_total}` : "—"))}
        </CardContent>
      </Card>
      <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(0, 1fr))` }}>
        {cols.map((c) => (
          <Card key={c.application_id}>
            <CardHeader><CardTitle className="text-sm">Top evidence · {c.name}</CardTitle></CardHeader>
            <CardContent>
              {c.top_evidence.length === 0 ? <p className="text-sm text-muted-foreground">None shown.</p> : (
                <ul className="space-y-3 text-sm">
                  {c.top_evidence.map((e) => (
                    <li key={e.criterion_id}>
                      <div className="font-medium">{e.name} <span className="text-xs font-normal text-muted-foreground">· {e.backing}</span></div>
                      {e.quote && <div className="text-xs text-muted-foreground">“{e.quote}”</div>}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
