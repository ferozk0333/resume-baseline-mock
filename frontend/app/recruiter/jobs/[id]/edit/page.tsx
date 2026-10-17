"use client";
import { Suspense } from "react";
import { useParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { JobFormPage } from "@/components/app/jobs/job-form";

export default function EditJob() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>;
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  return <JobFormPage jobId={id} />;
}
