"use client";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

/** Full window width for the recruiter workspace (specs/13 §1); a centered column everywhere else. */
export const isWorkspace = (path: string) => /^\/recruiter\/(jobs\/[^/]+|applications\/[^/]+\/workflow)$/.test(path);

export function AppMain({ children }: { children: React.ReactNode }) {
  const wide = isWorkspace(usePathname());
  return (
    <main className={cn("w-full flex-1", wide ? "px-4 py-3" : "mx-auto max-w-6xl px-6 py-8")}>{children}</main>
  );
}
