"use client";
import { Suspense } from "react";
import { useParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { PlanView } from "@/components/features/score/plan-view";

export default function PlanPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>;
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  return <PlanView id={id} />;
}
