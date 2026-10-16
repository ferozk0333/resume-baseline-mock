"use client";
import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(cb: () => void) {
  try {
    const m = window.matchMedia(QUERY);
    m.addEventListener("change", cb);
    return () => m.removeEventListener("change", cb);
  } catch { return () => {}; }
}

/** True when the visitor asked for less motion: no animation, instant state changes, no auto-advance. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => { try { return window.matchMedia(QUERY).matches; } catch { return false; } },
    () => false);
}
