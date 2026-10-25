"use client";
import { createContext, useContext } from "react";
import type { ProfileResponse, Role } from "@/lib/api";

export type Edit = {
  op: "add" | "edit" | "delete" | "add_back" | "undo" | "confirm_dates";
  item: "role" | "project" | "claim" | "certification" | "reference" | "dropped" | "mention" | "extra" | "deleted";
  id?: string; data?: Record<string, unknown>;
};

export type ReviewApi = {
  /** Send one edit; the updated profile on success, null on failure (a toast says why). */
  apply: (e: Edit, ok?: string) => Promise<ProfileResponse | null>;
  /** Delete (after a confirm, unless `ask` is false) with an "Undo" toast. */
  remove: (e: Edit, label: string, ask?: boolean) => Promise<void>;
  roles: Role[];
};

export const ReviewApiCtx = createContext<ReviewApi | null>(null);

export function useReviewApi(): ReviewApi {
  const c = useContext(ReviewApiCtx);
  if (!c) throw new Error("useReviewApi needs ReviewApiCtx");
  return c;
}

/** Dirty while editing: the draft differs from what was saved. */
export const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
