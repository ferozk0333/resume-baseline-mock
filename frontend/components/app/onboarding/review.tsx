"use client";
import { useEffect, useMemo } from "react";
import { AlertTriangle, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { AttentionBox } from "@/components/app/review/attention";
import { AddButton, CertificationCard, ProjectCard, ReferenceCard, RoleCard } from "@/components/app/review/cards";
import { ReviewEditProvider, useReviewEdit } from "@/components/app/review/edit-context";
import { EDUCATION, MoreProof, NotScored, ProofCard, proofCount, proofItems } from "@/components/app/review/more-proof";
import { ReviewApiCtx, type Edit, type ReviewApi } from "@/components/app/review/review-api";
import { SectionMenu, type MenuEntry } from "@/components/app/review/section-menu";
import { Skills } from "@/components/app/review/skills";
import { patch, type ProfileResponse } from "@/lib/api";

const PROJECT_KINDS = new Set(["project", "repo", "work_sample"]);

/** Review (specs/19 Phase B): one entry card for everything, a sticky section menu, "Needs your attention", and a
 * sticky "Confirm profile" bar. */
export function Review(props: {
  data: ProfileResponse; onChange: (d: ProfileResponse) => void; onConfirm: () => void; confirming: boolean;
  confirmLabel?: string;
}) {
  return <ReviewEditProvider><ReviewInner {...props} /></ReviewEditProvider>;
}

function ReviewInner({ data, onChange, onConfirm, confirming, confirmLabel = "Confirm & score" }: {
  data: ProfileResponse; onChange: (d: ProfileResponse) => void; onConfirm: () => void; confirming: boolean;
  confirmLabel?: string;
}) {
  const cid = data.candidate.id;
  const { editing, confirm } = useReviewEdit();
  const { roles, claims, certifications } = data.profile;
  const projects = data.profile.projects.filter((p) => PROJECT_KINDS.has(p.kind) && p.details?.proof_type !== "open_source");
  const extra = data.extra ?? [];
  const education = proofItems(EDUCATION, [], [], extra);
  const skills = claims.filter((c) => c.kind !== "award");
  const mentions = data.mentions ?? [];
  const references = data.profile.references ?? [];

  const api: ReviewApi = useMemo(() => {
    async function apply(edit: Edit, ok?: string) {
      try {
        const out = await patch<ProfileResponse>(`/candidates/${cid}/profile`, edit);
        onChange(out);
        if (ok) toast.success(ok);
        return out;
      } catch (e) {
        toast.error((e as Error).message);
        return null;
      }
    }
    async function remove(edit: Edit, label: string, ask = true) {
      if (ask && !(await confirm({ title: `Delete “${label}”?`, body: "You can undo this right after.",
        confirm: "Delete", cancel: "Keep it", destructive: true }))) return;
      const out = await apply(edit);
      const undo = out?.undo;
      if (undo) {
        toast(`Deleted “${label}”`, { duration: 8000,
          action: { label: "Undo", onClick: () => { apply({ op: "undo", item: "deleted", id: undo }, "Restored"); } } });
      }
    }
    return { apply, remove, roles };
  }, [cid, onChange, confirm, roles]);

  const proof = proofCount(data.profile.projects, claims, extra);
  const menu: MenuEntry[] = [
    { id: "sec-roles", label: "Roles", count: roles.length },
    { id: "sec-projects", label: "Projects", count: projects.length },
    { id: "sec-education", label: "Education", count: education.length },
    { id: "sec-skills", label: "Skills", count: skills.length },
    { id: "sec-certifications", label: "Certifications", count: certifications.length },
    { id: "sec-references", label: "References", count: references.length },
    { id: "sec-more", label: "More proof", count: proof },
    { id: "sec-application", label: "Application details", disabled: true, note: "After your scores" },
  ];
  useEffect(() => {          // "Add proof" after Mark done opens Review at a section (#sec-…): scroll there, move focus
    const hash = window.location.hash.slice(1);
    if (!hash.startsWith("sec-")) return;
    const section = document.getElementById(hash);
    section?.scrollIntoView({ block: "start" });
    section?.focus({ preventScroll: true });
  }, []);
  const reason = data.blocker ? data.blocker
    : data.reading ? "You can confirm when everything is read."
    : editing ? "Save or cancel the card you're editing first." : null;

  return (
    <ReviewApiCtx.Provider value={api}>
      <div className="grid gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <div className="hidden lg:block"><SectionMenu entries={menu} /></div>
        <div className="min-w-0 space-y-6">
          <AttentionBox data={data} />
          {data.warnings.length > 0 && (
            <Alert>
              <AlertTriangle />
              <AlertTitle>Some sources could not be fully read</AlertTitle>
              <AlertDescription><ul className="list-disc pl-4">{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></AlertDescription>
            </Alert>
          )}
          {(data.notices ?? []).map((n) => (
            <p key={n} className="rounded-lg border bg-card px-3 py-2 text-sm text-muted-foreground" data-testid="notice">{n}</p>
          ))}
          {mentions.length > 0 && (
            <ul className="space-y-1.5" data-testid="mentions" aria-label="Deleted items another document mentions">
              {mentions.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border bg-card px-3 py-2 text-sm">
                  <span>{m.source} also mentions <span className="font-medium">{m.label}</span></span>
                  <span className="text-xs text-muted-foreground">(you deleted it)</span>
                  <Button variant="outline" size="sm" className="ml-auto"
                    onClick={() => api.apply({ op: "add_back", item: "mention", id: m.id }, "Added back")}><Undo2 /> Add back</Button>
                </li>
              ))}
            </ul>
          )}

          <Section id="sec-roles" title="Roles" description="Your work experience. Edit anything that was read wrongly.">
            {roles.length === 0 && <p className="text-sm text-muted-foreground">No roles found.</p>}
            {roles.map((r) => <RoleCard key={r.id} role={r} />)}
            <AddButton id="new-role" label="Add role" render={(done) => <RoleCard role={null} onDone={done} />} />
          </Section>

          <Section id="sec-projects" title="Projects" description="Projects, repositories and work samples. Publications, talks and the like are under More proof.">
            {projects.length === 0 && <p className="text-sm text-muted-foreground">No projects found.</p>}
            {projects.map((p) => <ProjectCard key={p.id} project={p} />)}
            <AddButton id="new-project" label="Add project" render={(done) => <ProjectCard project={null} onDone={done} />} />
          </Section>

          <Section id="sec-education" title="Education" aside={<NotScored />}>
            {education.length === 0 && <p className="text-sm text-muted-foreground">No education found.</p>}
            {education.map((x) => <ProofCard key={x.id} t={EDUCATION} item={x} />)}
            <AddButton id="new-education" label="Add education" render={(done) => <ProofCard t={EDUCATION} item={null} onDone={done} />} />
          </Section>

          <Section id="sec-skills" title="Skills and tools" description="Named in your documents, grouped to make them easy to check.">
            <Skills claims={skills} groups={data.skill_groups ?? ["Other"]} />
          </Section>

          <Section id="sec-certifications" title="Certifications"
            description="Adding a credential ID or link makes a certificate count as stronger proof (Trust).">
            {certifications.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
            {certifications.map((c) => <CertificationCard key={c.id} cert={c} />)}
            <AddButton id="new-certification" label="Add certification" render={(done) => <CertificationCard cert={null} onDone={done} />} />
          </Section>

          <Section id="sec-references" title="References"
            description="A manager or supervisor confirming relevant work counts most (Trust).">
            {references.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
            {references.map((r) => <ReferenceCard key={r.id} reference={r} />)}
            <AddButton id="new-reference" label="Add reference" render={(done) => <ReferenceCard reference={null} onDone={done} />} />
          </Section>

          <Section id="sec-more" title="More proof" description="Publications, talks, awards and more. Add what applies to you.">
            <MoreProof projects={data.profile.projects} claims={claims} extra={extra} />
          </Section>

          <Section id="sec-sources" title="Source text" description="Each document as we read it, after clean-up. Quotes are checked against this text.">
            {data.sources.map((s) => (
              <details key={s.id} className="rounded-lg border bg-card">
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
                  {{ resume: "Resume", linkedin_pdf: "LinkedIn PDF", portfolio: "Portfolio" }[s.type] ?? s.type}
                  {s.status === "failed" && <span className="ml-2 text-destructive">could not be extracted</span>}
                </summary>
                <pre className="max-h-80 overflow-auto border-t px-3 py-2 text-xs whitespace-pre-wrap text-muted-foreground">{s.text}</pre>
              </details>
            ))}
          </Section>
        </div>
      </div>

      <div className="sticky bottom-0 z-20 -mx-6 mt-6 flex flex-wrap items-center justify-end gap-3 border-t bg-background/95 px-6 py-3 backdrop-blur" data-testid="confirm-bar">
        <SectionMenu entries={menu} variant="bar" />
        {reason && <p className={data.blocker ? "mr-auto text-sm text-destructive" : "mr-auto text-sm text-muted-foreground"}
          data-testid="confirm-wait" id="confirm-reason">{reason}</p>}
        <Button size="lg" onClick={onConfirm} disabled={!!reason || confirming} aria-describedby={reason ? "confirm-reason" : undefined}>
          {confirmLabel}
        </Button>
      </div>
    </ReviewApiCtx.Provider>
  );
}

function Section({ id, title, description, aside, children }: { id: string; title: string; description?: string;
  aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section id={id} tabIndex={-1} aria-labelledby={`${id}-title`} className="scroll-mt-[calc(var(--header-h)+1rem)] space-y-3 rounded-xl border bg-card p-4 outline-none sm:p-5">
      <div>
        <h2 id={`${id}-title`} className="flex flex-wrap items-baseline gap-x-2 text-base font-semibold">{title}{aside}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}
