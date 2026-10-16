"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { isWorkspace } from "@/components/app/app-main";
import { get, type RecruiterTask } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Open recruiter tasks, refreshed every 30 s and on navigation. */
function useTaskCount(active: boolean, path: string) {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    if (!active) return;
    const load = () => get<RecruiterTask[]>("/recruiter/tasks").then((t) => setN(t.length)).catch(() => setN(null));
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [active, path]);
  return n;
}

export function SiteHeader() {
  const path = usePathname();
  const mode = path.startsWith("/recruiter") ? "recruiter" : path.startsWith("/admin") ? "admin" : "candidate";
  const recruiter = mode === "recruiter";
  const tasks = useTaskCount(recruiter, path);
  const nav: { href: string; label: string; count?: number | null }[] = recruiter
    ? [{ href: "/recruiter", label: "Jobs" }, { href: "/recruiter/tasks", label: "Tasks", count: tasks },
      { href: "/recruiter/outbox", label: "Outbox" }]
    : mode === "admin" ? [{ href: "/admin/market", label: "Market baselines" }]
    : [{ href: "/", label: "Candidates" }, { href: "/baselines", label: "Baselines" },
      { href: "/market", label: "What the market asks for" }];
  return (
    <header className="sticky top-0 z-30 border-b bg-white/90 backdrop-blur">
      <div className={cn("mx-auto flex h-14 items-center gap-6", isWorkspace(path) ? "px-4" : "max-w-6xl px-6")}>
        <Link href="/" className="text-base font-semibold tracking-tight">
          Right<span className="text-primary">Staff</span>
        </Link>
        {(
          <nav aria-label="Main" className="flex items-center gap-1 text-sm">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground",
                  (n.href === "/" ? path === "/" || path.startsWith("/candidates") || path.startsWith("/runs")
                    : n.href === "/recruiter" ? path.startsWith("/recruiter") && !/^\/recruiter\/(outbox|tasks)/.test(path)
                    : path.startsWith(n.href)) && "bg-muted text-foreground",
                )}
              >
                {n.label}
                {!!n.count && (
                  <span aria-label={`${n.count} open`} className="ml-1.5 rounded-full bg-primary px-1.5 py-px text-[11px] font-semibold text-primary-foreground tabular-nums">{n.count}</span>
                )}
              </Link>
            ))}
          </nav>
        )}
        <div role="group" aria-label="View" className="ml-auto flex rounded-lg border bg-muted p-0.5 text-sm">
          {([["candidate", "/", "Candidate"], ["recruiter", "/recruiter", "Recruiter"], ["admin", "/admin/market", "Admin"]] as const).map(([m, href, label]) => (
            <Link key={m} href={href} aria-pressed={mode === m}
              className={cn("rounded-md px-3 py-1", mode === m ? "bg-white shadow-sm" : "text-muted-foreground")}>{label}</Link>
          ))}
        </div>
      </div>
    </header>
  );
}
