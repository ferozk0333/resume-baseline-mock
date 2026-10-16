"use client";
import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { ScoreView } from "@/components/app/score/score-view";

export default function ScorePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>;
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  const technical = useSearchParams().get("technical") === "1";        // Admin → Demo tools
  return <ScoreView id={id} technical={technical} />;
}
