"use client";
// The demo has no sign-in (specs/17 §2): the candidate being viewed is remembered in this browser only.
import { useCallback, useSyncExternalStore } from "react";

const KEY = "rightstaff.currentCandidate";
const EVENT = "rightstaff-candidate";

function read(): string | null {
  try { return window.localStorage.getItem(KEY); } catch { return null; }
}

export function setCurrentCandidate(id: string | null) {
  try {
    if (id) window.localStorage.setItem(KEY, id); else window.localStorage.removeItem(KEY);
  } catch { /* private window or blocked storage: the choice lasts for this page only */ }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener("storage", cb); };
}

/** [candidate id or null, ready]. `ready` is false during server rendering (storage not read yet). */
export function useCurrentCandidate(): [string | null, boolean, (id: string | null) => void] {
  const id = useSyncExternalStore(subscribe, read, () => undefined as unknown as string | null);
  const set = useCallback((v: string | null) => setCurrentCandidate(v), []);
  return [id ?? null, id !== undefined, set];
}
