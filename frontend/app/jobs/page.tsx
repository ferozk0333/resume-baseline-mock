"use client";
import { Suspense, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { JobsBrowser } from "@/components/app/candidate/jobs-browser";
import { useCurrentCandidate } from "@/lib/current-candidate";

export default function JobsPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Jobs /></Suspense>;
}

/** Jobs (specs/17 §4): the same ranked list as Home, without the score strip. */
function Jobs() {
  const router = useRouter();
  const [cid, ready] = useCurrentCandidate();
  useEffect(() => { if (ready && !cid) router.replace("/onboarding"); }, [ready, cid, router]);
  if (!ready || !cid) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
      <JobsBrowser cid={cid} />
    </div>
  );
}
