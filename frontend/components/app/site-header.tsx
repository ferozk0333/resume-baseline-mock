"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { isWorkspace } from "@/components/app/app-main";
import { cn } from "@/lib/utils";

export function SiteHeader() {
  const path = usePathname();
  const recruiter = path.startsWith("/recruiter");
  const nav = recruiter ? [{ href: "/recruiter", label: "Jobs" }, { href: "/recruiter/outbox", label: "Outbox" }] : [
    { href: "/", label: "Candidates" },
    { href: "/baselines", label: "Baselines" },
  ];
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
                    : n.href === "/recruiter" ? path.startsWith("/recruiter") && !path.startsWith("/recruiter/outbox")
                    : path.startsWith(n.href)) && "bg-muted text-foreground",
                )}
              >
                {n.label}
              </Link>
            ))}
          </nav>
        )}
        <div role="group" aria-label="View" className="ml-auto flex rounded-lg border bg-muted p-0.5 text-sm">
          <Link href="/" aria-pressed={!recruiter}
            className={cn("rounded-md px-3 py-1", !recruiter ? "bg-white shadow-sm" : "text-muted-foreground")}>
            Candidate
          </Link>
          <Link href="/recruiter" aria-pressed={recruiter}
            className={cn("rounded-md px-3 py-1", recruiter ? "bg-white shadow-sm" : "text-muted-foreground")}>
            Recruiter
          </Link>
        </div>
      </div>
    </header>
  );
}
