"use client";

/** The error state of a candidate list or page: what failed and a retry. */
export function LoadError({ what, error, onRetry }: { what: string; error: string; onRetry: () => void }) {
  return (
    <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      {what} could not be loaded: {error}{" "}
      <button type="button" className="font-medium underline" onClick={onRetry}>Try again</button>
    </p>
  );
}
