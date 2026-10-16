"use client";
import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { JobDetailView } from "@/components/app/candidate/job-detail";
import { useCurrentCandidate } from "@/lib/current-candidate";

export default function JobPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Job /></Suspense>;
}

/** A job as its own page (narrow screens open jobs here; desktop shows the same view in the Home pane). */
function Job() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [cid, ready] = useCurrentCandidate();
  useEffect(() => { if (ready && !cid) router.replace("/onboarding"); }, [ready, cid, router]);
  if (!ready || !cid) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <Link href="/jobs" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All jobs</Link>
      <JobDetailView cid={cid} jobId={id} />
    </div>
  );
}
