// Friendly dates in Review (specs/19 item 7): "Oct 2025 – Jun 2026", "Oct 2025 – Present", "2024" when year-only.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2025-10-01" or "2025-10" → "Oct 2025"; `yearOnly` → "2025". */
export function friendlyMonth(d: string | null | undefined, yearOnly = false): string {
  if (!d) return "";
  const [y, m] = d.split("-");
  if (yearOnly || !m) return y;
  return `${MONTHS[Number(m) - 1] ?? m} ${y}`;
}

export function friendlyRange(start: string | null, end: string | null, current: boolean,
  codes: string[] = []): string {
  const s = friendlyMonth(start, codes.includes("year_only_start"));
  const e = current ? "Present" : friendlyMonth(end, codes.includes("year_only_end"));
  if (!s && !e) return "";
  if (s && e && s === e) return s;
  return [s || "?", e || "?"].join(" – ");
}

/** The month picker's value ("YYYY-MM"). */
export const monthValue = (d: string | null | undefined) => (d ? d.slice(0, 7) : "");
