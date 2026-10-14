"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Briefcase, Users } from "lucide-react";
import { toast } from "sonner";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { get, type Job } from "@/lib/api";

export default function RecruiterHome() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  useEffect(() => { get<Job[]>("/jobs").then(setJobs).catch((e) => toast.error(e.message)); }, []);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Open jobs</h1>
        <p className="text-sm text-muted-foreground">Applicants ranked by match score. Pick a job to review them.</p>
      </div>
      {jobs && jobs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No jobs yet. Seed the demo with <code className="rounded bg-muted px-1">python scripts/seed_recruiter_demo.py</code>.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-3">
        {(jobs ?? []).map((j) => (
          <Link key={j.id} href={`/recruiter/jobs/${j.id}`} className="rounded-xl focus-visible:outline-2 focus-visible:outline-ring">
            <Card className="h-full transition-shadow hover:shadow-md">
              <CardHeader>
                <Briefcase className="size-5 text-primary" />
                <CardTitle className="text-base">{j.title}</CardTitle>
                <CardDescription>{j.company}</CardDescription>
                <div className="flex items-center gap-1.5 pt-2 text-sm text-muted-foreground">
                  <Users className="size-4" /> {j.applicants} applicant{j.applicants === 1 ? "" : "s"}
                </div>
              </CardHeader>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
