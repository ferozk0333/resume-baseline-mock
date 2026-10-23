"use client";
import { useEffect, useState } from "react";
import { FileDown } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PostingPreview } from "@/components/app/jobs/posting-preview";
import { API, get, type Posting } from "@/lib/api";
import { LinkedText } from "@/components/app/ext-link";

/** The job page's Job description tab (specs/14 §5): the posting candidates see and the original JD. */
export function JobDescription({ jobId }: { jobId: string }) {
  const [p, setP] = useState<Posting | null>(null);
  useEffect(() => { get<Posting>(`/jobs/${jobId}/posting`).then(setP).catch((e) => toast.error(e.message)); }, [jobId]);
  if (!p) return <Skeleton className="h-96" />;
  return (
    <div className="grid min-h-0 flex-1 gap-3 overflow-y-auto lg:grid-cols-2">
      <section className="h-fit rounded-xl border bg-white p-5">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Posting (what candidates see)</h2>
          <Badge variant={p.status === "open" ? "default" : "secondary"} className="capitalize">{p.status}</Badge>
        </div>
        <PostingPreview p={p} />
      </section>
      <section className="h-fit rounded-xl border bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Original job description</h2>
          {p.has_jd_file && <a href={`${API}/jobs/${jobId}/jd-file`} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"><FileDown className="size-4" /> File</a>}
        </div>
        {p.jd_text ? <pre className="font-[inherit] text-xs leading-relaxed whitespace-pre-wrap"><LinkedText text={p.jd_text} /></pre>
          : <p className="text-sm text-muted-foreground">This job was not created from a job description.</p>}
      </section>
    </div>
  );
}
