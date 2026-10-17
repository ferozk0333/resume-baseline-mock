"use client";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { FlaskConical } from "lucide-react";
import { get } from "@/lib/api";

/** Candidate side on the demo API: say plainly that uploads are not read and scores are scripted. */
export function ModeBanner() {
  const path = usePathname();
  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => { get<{ mode: string }>("/config/mode").then((m) => setMode(m.mode)).catch(() => {}); }, []);
  if (mode !== "demo" || path.startsWith("/recruiter")) return null;
  return (
    <div role="status" className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-center text-sm text-amber-900">
      <FlaskConical className="mr-1.5 inline size-4" />
      Demo mode: uploads are not read. Reading, scoring and the plan are scripted, so every profile gets the same
      preset scores. For real processing, stop the demo and run <code className="font-mono">make live</code>.
    </div>
  );
}
