"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, CircleAlert } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DetailsForm } from "@/components/app/candidate/details-form";
import { LoadError } from "@/components/app/candidate/load-error";
import { get, put, type ApplicationDetails, type OnboardingState, type ProfileResponse } from "@/lib/api";
import { useCurrentCandidate } from "@/lib/current-candidate";

export default function ProfilePage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><Profile /></Suspense>;
}

const SOURCE: Record<string, string> = { resume: "Resume", linkedin_pdf: "LinkedIn profile (PDF)", portfolio: "Portfolio" };

/** Profile (specs/17 §8): your sources, review again, application details. */
function Profile() {
  const router = useRouter();
  const [cid, ready] = useCurrentCandidate();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [details, setDetails] = useState<ApplicationDetails | null>(null);

  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!cid) return;
    get<OnboardingState>(`/candidates/${cid}/onboarding`).then(setState).catch((e) => setError(e.message));
    get<ProfileResponse>(`/candidates/${cid}/profile`).then(setProfile).catch((e) => setError(e.message));
    get<ApplicationDetails>(`/candidates/${cid}/application-details`).then(setDetails).catch((e) => setError(e.message));
  }, [cid]);
  useEffect(() => {
    if (!ready) return;
    if (!cid) router.replace("/onboarding"); else load();
  }, [ready, cid, router, load]);
  useEffect(() => {                                        // "Add details" on Home links here
    if (details && window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
  }, [details]);

  async function restart(step: "proof" | "review") {
    try { await put(`/candidates/${cid}/onboarding`, { step }); router.push("/onboarding"); }
    catch (e) { toast.error((e as Error).message); }
  }

  if (error) return <LoadError what="Your profile" error={error} onRetry={() => { setError(null); load(); }} />;
  if (!ready || !cid || !state || !profile || !details) return <Skeleton className="h-96" />;
  const p = profile.profile;
  const c = state.candidate as OnboardingState["candidate"] & { github_username?: string | null; portfolio_url?: string | null };
  const rows: [string, string | null, boolean][] = [
    ...profile.sources.map((s) => [SOURCE[s.type] ?? s.type, s.status === "failed" ? "Could not be read" : "Read", s.status !== "failed"] as [string, string, boolean]),
    ["GitHub", c.github_username ?? null, !!c.github_username],
    ["Project demo videos", profile.media_links.length ? `${profile.media_links.length} linked` : null, profile.media_links.length > 0],
    ["Certifications", p.certifications.length ? `${p.certifications.length}` : null, p.certifications.length > 0],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <p className="text-sm text-muted-foreground">{c.name}{state.targets.length > 0 && ` · aiming for ${state.targets.map((t) => t.title).join(", ")}`}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your proof</CardTitle>
          <CardDescription>
            What your scores are built from: {p.roles.length} roles, {p.projects.length} projects and {p.claims.length} skills and tools.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ul className="divide-y rounded-lg border">
            {rows.map(([label, value, ok]) => (
              <li key={label} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="flex items-center gap-2">
                  {ok ? <CheckCircle2 className="size-4 text-emerald-600" aria-hidden /> : <CircleAlert className="size-4 text-muted-foreground" aria-hidden />}
                  {label}
                </span>
                <span className="truncate text-muted-foreground">{value ?? "Not added"}</span>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => restart("proof")}>Update your documents</Button>
            <Button variant="outline" onClick={() => restart("review")}>Review your profile again</Button>
            <Link href="/scores" className={buttonVariants({ variant: "ghost" })}>My scores</Link>
          </div>
          <p className="text-xs text-muted-foreground">Both re-score your target roles when you confirm.</p>
        </CardContent>
      </Card>

      <section id="application-details" className="scroll-mt-20 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">Application details</h2>
          {details.saved_at ? <Badge variant="secondary">Saved {new Date(details.saved_at).toLocaleDateString()}</Badge>
            : <Badge variant="outline">Not filled in yet</Badge>}
        </div>
        <DetailsForm candidateId={cid} initial={details} onSaved={setDetails} />
      </section>
    </div>
  );
}
