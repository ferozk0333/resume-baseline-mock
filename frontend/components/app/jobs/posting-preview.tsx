import { Briefcase, MapPin } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { WORKPLACE, type Posting } from "@/lib/api";
import { LinkedText } from "@/components/app/ext-link";

/** The posting as candidates see it (create-job preview, the job page's Job description tab, candidate Home). */
export function PostingPreview({ p, compact }: { p: Pick<Posting, "title" | "company" | "department" | "location" |
  "workplace" | "employment_type" | "level" | "min_years" | "short_description" | "responsibilities" | "required" |
  "preferred">; compact?: boolean }) {
  const facts = [p.location, p.workplace ? WORKPLACE[p.workplace] : null, p.employment_type,
    p.level ? `${p.level[0].toUpperCase()}${p.level.slice(1)} level` : null,
    p.min_years !== null && p.min_years !== undefined ? `${p.min_years}+ years` : null].filter(Boolean);
  return (
    <article aria-label="Candidate preview" className="space-y-3 text-sm">
      <header>
        <h3 className="text-lg font-semibold">{p.title || "Job title"}</h3>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
          <span className="inline-flex items-center gap-1"><Briefcase className="size-3.5" />{p.company || "Company"}{p.department ? ` · ${p.department}` : ""}</span>
          {facts.length > 0 && <span className="inline-flex items-center gap-1"><MapPin className="size-3.5" />{facts.join(" · ")}</span>}
        </p>
      </header>
      {p.short_description && <p><LinkedText text={p.short_description} /></p>}
      {!compact && p.responsibilities?.length > 0 && (
        <section><h4 className="font-medium">What you&apos;ll do</h4>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{p.responsibilities.map((r, i) => <li key={i}><LinkedText text={r} /></li>)}</ul></section>
      )}
      <section><h4 className="font-medium">Required</h4>
        <div className="mt-1 flex flex-wrap gap-1">{p.required.length ? p.required.map((r) => <Badge key={r} variant="secondary">{r}</Badge>)
          : <span className="text-muted-foreground">None listed</span>}</div></section>
      {p.preferred.length > 0 && (
        <section><h4 className="font-medium">Nice to have</h4>
          <div className="mt-1 flex flex-wrap gap-1">{p.preferred.map((r) => <Badge key={r} variant="outline">{r}</Badge>)}</div></section>
      )}
    </article>
  );
}
