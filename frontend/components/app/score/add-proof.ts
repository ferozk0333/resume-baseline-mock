import { toast } from "sonner";
import { put } from "@/lib/api";

/** "Add proof" after Mark done (Home and the plan page): the Review section that holds each kind of proof (specs/19).
 * Review lives in onboarding, so the button reopens it at that section, the same way Profile's "Review your profile
 * again" does. Nothing is deleted; confirming the profile there re-scores the target roles. Marking done never changes a
 * score. */
export const PROOF_SECTION: Record<string, string> = {
  build_project: "sec-projects",
  publish_work: "sec-projects",
  contribute_or_compete: "sec-more",
  earn_certification: "sec-certifications",
  get_reference: "sec-references",
};
export const ADD_PROOF_HINT = "Add the proof to your profile.";
export const THEN_CONFIRM = "Then confirm your profile to update your score.";

/** The line shown after an action is marked done, without the "Add proof" button. */
export function doneHint(type: string): string | null {
  return PROOF_SECTION[type] ? `${ADD_PROOF_HINT} ${THEN_CONFIRM}` : null;
}

export async function openProof(cid: string, type: string, push: (href: string) => void) {
  const section = PROOF_SECTION[type];
  if (!section) return;
  try { await put(`/candidates/${cid}/onboarding`, { step: "review" }); push(`/onboarding#${section}`); }
  catch (e) { toast.error((e as Error).message); }
}

export function markedDoneToast(type: string, onAddProof: () => void, onUndo: () => void) {
  const undo = { label: "Undo", onClick: onUndo };
  const description = doneHint(type) ?? undefined;
  if (PROOF_SECTION[type]) toast.success("Marked done", { description, action: { label: "Add proof", onClick: onAddProof }, cancel: undo });
  else toast.success("Marked done", { description, action: undo });
}
