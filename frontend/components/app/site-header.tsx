"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { isWorkspace } from "@/components/app/app-main";
import { ViewingAs } from "@/components/app/candidate/switch-profile";
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
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {                      // the header wraps on narrower screens: sticky content sits below its real height
    const el = ref.current;
    if (!el) return;
    const set = () => document.documentElement.style.setProperty("--header-h", `${el.offsetHeight}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Admin: market pipeline, role baselines and the demo tools (the old "Score a profile" page and its flow).
  const mode = path.startsWith("/recruiter") ? "recruiter"
    : /^\/(admin|baselines|candidates)(\/|$)/.test(path) ? "admin" : "candidate";
  const recruiter = mode === "recruiter";
  const tasks = useTaskCount(recruiter, path);
  const nav: { href: string; label: string; count?: number | null }[] = recruiter
    ? [{ href: "/recruiter", label: "Jobs" }, { href: "/recruiter/tasks", label: "Tasks", count: tasks },
      { href: "/recruiter/outbox", label: "Outbox" }]
    : mode === "admin" ? [{ href: "/admin/market", label: "Market baselines" }, { href: "/baselines", label: "Baselines" },
      { href: "/admin/screening", label: "Screening" }, { href: "/admin/demo", label: "Demo tools" }]
    : [{ href: "/", label: "Home" }, { href: "/jobs", label: "Jobs" }, { href: "/applications", label: "My applications" },
      { href: "/scores", label: "My scores" }, { href: "/profile", label: "Profile" }];
  const active = (href: string) => href === "/" ? path === "/" || path === "/onboarding"
    : href === "/scores" ? /^\/(scores|runs|market)(\/|$)/.test(path)
    : href === "/recruiter" ? path.startsWith("/recruiter") && !/^\/recruiter\/(outbox|tasks)/.test(path)
    : href === "/admin/demo" ? path.startsWith("/admin/demo") || path.startsWith("/candidates")
    : path.startsWith(href);
  return (
    <header ref={ref} className="sticky top-0 z-30 border-b bg-white">
      <div className={cn("mx-auto flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 py-2 lg:flex-nowrap xl:gap-x-6",
        isWorkspace(path) ? "px-4" : "max-w-6xl px-4 sm:px-6")}>
        <Link href={mode === "recruiter" ? "/recruiter" : mode === "admin" ? "/admin/market" : "/"} className="text-base font-semibold tracking-tight">
          Right<span className="text-primary">Staff</span>
        </Link>
        {(
          <nav aria-label="Main" className="order-3 flex w-full flex-wrap items-center gap-1 text-sm sm:order-none sm:w-auto lg:flex-nowrap">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active(n.href) ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1.5 whitespace-nowrap text-muted-foreground hover:bg-muted hover:text-foreground xl:px-3",
                  active(n.href) && "bg-muted text-foreground",
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
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-1 lg:flex-nowrap xl:gap-x-4">
          {mode === "candidate" && <ViewingAs />}
          <div role="group" aria-label="View" className="flex rounded-lg border bg-muted p-0.5 text-sm">
            {([["candidate", "/", "Candidate"], ["recruiter", "/recruiter", "Recruiter"], ["admin", "/admin/market", "Admin"]] as const).map(([m, href, label]) => (
              <Link key={m} href={href} aria-pressed={mode === m}
                className={cn("rounded-md px-3 py-1", mode === m ? "bg-white shadow-sm" : "text-muted-foreground")}>{label}</Link>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}
