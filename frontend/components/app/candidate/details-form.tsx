"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { put, type ApplicationDetails } from "@/lib/api";

type Opt = [string, string];
const DECLINE: Opt = ["decline", "Decline to answer"];
const YES_NO: Opt[] = [["yes", "Yes"], ["no", "No"]];
const NOTICE: Opt[] = [["immediately", "Available now"], ["1_week", "1 week"], ["2_weeks", "2 weeks"], ["1_month", "1 month"],
  ["2_months", "2 months"], ["3_months_plus", "3 months or more"]];
const MODES: Opt[] = [["remote", "Remote"], ["hybrid", "Hybrid"], ["on_site", "On-site"], ["no_preference", "No preference"]];
const EEO: { key: keyof ApplicationDetails["eeo"]; label: string; options: Opt[] }[] = [
  { key: "gender", label: "Gender", options: [["woman", "Woman"], ["man", "Man"], ["non_binary", "Non-binary"],
    ["self_describe", "I prefer to self-describe"], DECLINE] },
  { key: "race_ethnicity", label: "Race / ethnicity", options: [["american_indian_alaska_native", "American Indian or Alaska Native"],
    ["asian", "Asian"], ["black_african_american", "Black or African American"], ["hispanic_latino", "Hispanic or Latino"],
    ["native_hawaiian_pacific_islander", "Native Hawaiian or Other Pacific Islander"], ["white", "White"],
    ["two_or_more", "Two or more races"], DECLINE] },
  { key: "veteran_status", label: "Veteran status", options: [["protected_veteran", "I am a protected veteran"],
    ["not_a_veteran", "I am not a protected veteran"], DECLINE] },
  { key: "disability_status", label: "Disability status", options: [["yes", "Yes, I have a disability (or had one)"],
    ["no", "No, I don't have a disability"], DECLINE] },
];

function Select({ id, label, value, options, onChange, blank = "Choose…" }: { id: string; label: string;
  value: string | null; options: Opt[]; onChange: (v: string | null) => void; blank?: string | null }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}
        className="h-9 w-full rounded-lg border border-field-border bg-field px-2 text-sm focus-visible:outline-2 focus-visible:outline-ring">
        {blank !== null && <option value="">{blank}</option>}
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

const list = (s: string) => s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

/** Application details, filled once (specs/17 §3.2). Only the candidate sees them; they are never sent to a model,
 * never shown to recruiters and never used in any score. */
export function DetailsForm({ candidateId, initial, onSaved, extra, saveLabel = "Save" }: {
  candidateId: string; initial: ApplicationDetails; onSaved: (d: ApplicationDetails) => void;
  extra?: React.ReactNode; saveLabel?: string;
}) {
  const [d, setD] = useState(initial);
  const [locations, setLocations] = useState(initial.questions.locations.join(", "));
  const [links, setLinks] = useState(initial.questions.links.join("\n"));
  const [busy, setBusy] = useState(false);
  const wa = (patch: Partial<ApplicationDetails["work_authorization"]>) =>
    setD({ ...d, work_authorization: { ...d.work_authorization, ...patch } });
  const q = (patch: Partial<ApplicationDetails["questions"]>) => setD({ ...d, questions: { ...d.questions, ...patch } });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const out = await put<ApplicationDetails>(`/candidates/${candidateId}/application-details`,
        { ...d, questions: { ...d.questions, locations: list(locations), links: list(links) } });
      toast.success("Application details saved");
      onSaved(out);
    } catch (err) { toast.error((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={save} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Work authorization</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="wa-country">Country you want to work in</Label>
            <Input id="wa-country" value={d.work_authorization.country} onChange={(e) => wa({ country: e.target.value })}
              placeholder="e.g. United States" />
          </div>
          <Select id="wa-authorized" label="Authorized to work there" value={d.work_authorization.authorized}
            options={YES_NO} onChange={(v) => wa({ authorized: v as "yes" | "no" | null })} />
          <Select id="wa-now" label="Needs visa sponsorship now" value={d.work_authorization.sponsorship_now} blank={null}
            options={[...YES_NO, ["decline", "Prefer not to say"]]} onChange={(v) => wa({ sponsorship_now: (v ?? "decline") as "decline" })} />
          <Select id="wa-future" label="Needs visa sponsorship in future" value={d.work_authorization.sponsorship_future} blank={null}
            options={[...YES_NO, ["decline", "Prefer not to say"]]} onChange={(v) => wa({ sponsorship_future: (v ?? "decline") as "decline" })} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Frequent questions</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="q-start">Earliest start date</Label>
            <Input id="q-start" type="date" value={d.questions.earliest_start ?? ""}
              onChange={(e) => q({ earliest_start: e.target.value || null })} />
          </div>
          <Select id="q-notice" label="Notice period" value={d.questions.notice_period} options={NOTICE}
            onChange={(v) => q({ notice_period: v })} />
          <div className="space-y-1.5">
            <Label htmlFor="q-salary">Minimum salary expectation (per year)</Label>
            <div className="flex gap-2">
              <Input id="q-salary" type="number" min={0} step={1000} value={d.questions.salary_min ?? ""}
                onChange={(e) => q({ salary_min: e.target.value === "" ? null : Number(e.target.value) })} />
              <Input aria-label="Currency" className="w-20" maxLength={3} value={d.questions.salary_currency}
                onChange={(e) => q({ salary_currency: e.target.value.toUpperCase() })} />
            </div>
          </div>
          <Select id="q-relocate" label="Willing to relocate" value={d.questions.relocate} options={YES_NO}
            onChange={(v) => q({ relocate: v as "yes" | "no" | null })} />
          <Select id="q-mode" label="Work mode preference" value={d.questions.work_mode} options={MODES}
            onChange={(v) => q({ work_mode: v })} />
          <div className="space-y-1.5">
            <Label htmlFor="q-locations">Preferred locations</Label>
            <Input id="q-locations" value={locations} onChange={(e) => setLocations(e.target.value)}
              placeholder="e.g. Austin, Remote (US)" />
            <p className="text-xs text-muted-foreground">Separate with commas.</p>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="q-links">Links (personal site, writing, talks)</Label>
            <textarea id="q-links" rows={2} value={links} onChange={(e) => setLinks(e.target.value)}
              placeholder="https://…" className="w-full rounded-lg border border-field-border bg-field px-2.5 py-1.5 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
            <p className="text-xs text-muted-foreground">One per line.</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Voluntary self-identification</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {EEO.map((f) => (
            <Select key={f.key} id={`eeo-${f.key}`} label={f.label} value={d.eeo[f.key]} options={f.options} blank={null}
              onChange={(v) => setD({ ...d, eeo: { ...d.eeo, [f.key]: v ?? "decline" } })} />
          ))}
        </CardContent>
      </Card>

      <div className="flex flex-wrap justify-end gap-2">
        {extra}
        <Button type="submit" disabled={busy}>{saveLabel}</Button>
      </div>
    </form>
  );
}
