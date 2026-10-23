import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { hrefFor, splitLinks } from "@/lib/links";

/** Every link to a stored or typed URL goes through here (audit round, item 4): the href is the URL exactly as
 * stored (lib/links.ts hrefFor), never rebuilt or re-encoded. `children` is the visible text (default: the URL). */
export function ExtLink({ url, children, className, icon = false, newTab = true }: {
  url: string; children?: ReactNode; className?: string; icon?: boolean; newTab?: boolean }) {
  return (
    <a href={hrefFor(url)} data-source-url={url} {...(newTab ? { target: "_blank", rel: "noreferrer" } : {})}
      className={cn("text-primary underline-offset-2 hover:underline", icon && "inline-flex items-center gap-1",
        children === undefined && "break-all", className)}>
      {children ?? url}{icon && <ExternalLink className="size-3 shrink-0" aria-hidden />}
    </a>
  );
}

/** Text with its links made clickable: Markdown "[text](url)" and plain URLs, read with balanced parentheses;
 * trailing punctuation stays text. The text wraps; a long URL breaks anywhere rather than overflowing. */
export function LinkedText({ text, className }: { text: string | null | undefined; className?: string }) {
  if (!text) return null;
  return (
    <span className={className}>
      {splitLinks(text).map((s, i) => s.url ? <ExtLink key={i} url={s.url} className="break-all">{s.text}</ExtLink>
        : <span key={i}>{s.text}</span>)}
    </span>
  );
}
