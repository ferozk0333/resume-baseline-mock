"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MoreHorizontal, Plus } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { del, get, post, WORKPLACE, type JobForm, type JobRow, type JobStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const href = (j: JobRow) => (j.status === "draft" ? `/recruiter/jobs/${j.id}/edit` : `/recruiter/jobs/${j.id}`);
const TABS: { key: JobStatus; label: string }[] = [{ key: "open", label: "Open" }, { key: "draft", label: "Drafts" },
  { key: "closed", label: "Closed" }];

/** Jobs (specs/14 §2): Open · Drafts · Closed, Create job, and a row menu (Edit, Duplicate, Close/Reopen). */
export default function JobsPage() {
  const router = useRouter();
  const [tab, setTab] = useState<JobStatus>("open");
  const [jobs, setJobs] = useState<JobRow[] | null>(null);
  // The row menu floats (fixed position from the ⋯ button) so the table's scroll container never clips it; it opens
  // upward when there is no room below.
  const [menu, setMenu] = useState<{ id: string; top: number; right: number; up: boolean } | null>(null);
  const load = useCallback(() => {
    get<JobRow[]>("/recruiter/jobs").then(setJobs).catch((e) => toast.error(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  const shown = (jobs ?? []).filter((j) => j.status === tab);
  const count = (s: JobStatus) => (jobs ?? []).filter((j) => j.status === s).length;

  async function duplicate(id: string) {
    try {
      const f = await post<JobForm>("/jobs/drafts/copy", { kind: "job", id });
      router.push(`/recruiter/jobs/${f.job_id}/edit`);
    } catch (e) { toast.error((e as Error).message); }
  }
  async function setStatus(id: string, status: "open" | "closed") {
    try { await post(`/jobs/${id}/status`, { status }); toast.success(status === "closed" ? "Job closed" : "Job reopened"); load(); }
    catch (e) { toast.error((e as Error).message); }
  }
  async function remove(id: string) {
    try { await del(`/jobs/${id}/draft`); load(); } catch (e) { toast.error((e as Error).message); }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          <p className="text-sm text-muted-foreground">Applicants are ranked by match score. Open a job to review them.</p>
        </div>
        <Link href="/recruiter/jobs/new" className={buttonVariants()}><Plus /> Create job</Link>
      </div>
      <div role="tablist" aria-label="Job status" className="flex w-fit rounded-lg border bg-muted p-0.5 text-sm">
        {TABS.map((t) => (
          <button key={t.key} role="tab" type="button" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
            className={cn("rounded-md px-3 py-1", tab === t.key ? "bg-white shadow-sm" : "text-muted-foreground")}>
            {t.label} <span className="tabular-nums text-muted-foreground">{jobs ? count(t.key) : ""}</span>
          </button>
        ))}
      </div>
      {jobs === null ? <Skeleton className="h-48" /> : shown.length === 0 ? (
        <p className="rounded-xl border border-dashed bg-white px-4 py-8 text-center text-sm text-muted-foreground">
          {tab === "draft" ? "No drafts. Create job saves a draft as you go." : tab === "closed" ? "No closed jobs." : "No open jobs yet."}
        </p>
      ) : (
        <div className="rounded-xl border bg-white">
          <Table aria-label={`${TABS.find((t) => t.key === tab)!.label} jobs`}>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead><TableHead>Company</TableHead><TableHead>Location</TableHead>
                <TableHead className="text-right">Applicants</TableHead><TableHead>Created</TableHead><TableHead>Status</TableHead>
                <TableHead className="w-10"><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((j) => (
                <TableRow key={j.id} onClick={() => router.push(href(j))} className="cursor-pointer hover:bg-muted/50">
                  <TableCell className="font-medium">
                    <Link href={href(j)} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                      {j.title || "Untitled job"}
                    </Link>
                    {j.has_draft && <Badge variant="outline" className="ml-2 text-[10px]">unsaved edit</Badge>}
                  </TableCell>
                  <TableCell>{j.company || "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{[j.location, j.workplace ? WORKPLACE[j.workplace] : null].filter(Boolean).join(" · ") || "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{j.applicants}</TableCell>
                  <TableCell className="text-muted-foreground">{new Date(j.created_at).toLocaleDateString()}</TableCell>
                  <TableCell>
                    <Badge variant={j.status === "open" ? "default" : "secondary"} className="capitalize">
                      {j.status === "draft" && j.step ? `Draft · step ${j.step} of 4` : j.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="relative" onClick={(e) => e.stopPropagation()}>
                    <Button size="icon-sm" variant="ghost" aria-label={`Actions for ${j.title || "draft"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        const r = e.currentTarget.getBoundingClientRect();
                        const up = window.innerHeight - r.bottom < 180;
                        setMenu(menu?.id === j.id ? null : { id: j.id, top: up ? r.top - 4 : r.bottom + 4,
                          right: window.innerWidth - r.right, up });
                      }}><MoreHorizontal /></Button>
                    {menu?.id === j.id && (
                      <div role="menu" aria-label={`Actions for ${j.title || "draft"}`}
                        style={{ position: "fixed", top: menu.top, right: menu.right, transform: menu.up ? "translateY(-100%)" : undefined }}
                        className="z-50 w-36 rounded-lg border bg-popover p-1 text-sm shadow-md">
                        <MenuItem onClick={() => router.push(`/recruiter/jobs/${j.id}/edit`)}>Edit</MenuItem>
                        {j.status !== "draft" && <MenuItem onClick={() => duplicate(j.id)}>Duplicate</MenuItem>}
                        {j.status === "open" && <MenuItem onClick={() => setStatus(j.id, "closed")}>Close</MenuItem>}
                        {j.status === "closed" && <MenuItem onClick={() => setStatus(j.id, "open")}>Reopen</MenuItem>}
                        {j.status === "draft" && <MenuItem onClick={() => remove(j.id)}>Delete draft</MenuItem>}
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function MenuItem({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button role="menuitem" type="button" onClick={onClick}
    className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-muted">{children}</button>;
}
