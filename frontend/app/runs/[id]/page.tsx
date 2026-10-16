"use client";
import { Suspense } from "react";
import { useParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { ScoreView } from "@/components/app/score/score-view";

export default function ScorePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>;
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  return <ScoreView id={id} />;
}
