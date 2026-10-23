// Links exactly as written (audit round, item 4). The same rules as core/links.py; tests/test_links.py runs one
// list of cases through both.
// - A URL keeps every character: ( ) are not percent-encoded and an existing %XX is not encoded again.
// - Balanced ( ) and [ ] inside a URL are kept; a bracket that closes the text around the link is not.
// - Trailing sentence punctuation (. , ; : ! ? and closing quotes) is not part of the URL.
// - Markdown "[text](url)" is read with balanced parentheses in the URL.

const START = /\b(?:https?:\/\/|www\.)/gi;
const STOP = new Set([" ", "\t", "\r", "\n", "<", ">", '"', "`", "{", "}", "|", "\\", "^"]);
const TRAILING = new Set([".", ",", ";", ":", "!", "?", "'", '"', "”", "’", "»"]);
const CLOSE: Record<string, string> = { ")": "(", "]": "[" };

function endOfUrl(text: string, start: number): number {
  let i = start;
  const depth: Record<string, number> = { "(": 0, "[": 0 };
  while (i < text.length && !STOP.has(text[i])) {
    const c = text[i];
    if (c in depth) depth[c] += 1;
    else if (c in CLOSE) {
      if (depth[CLOSE[c]] === 0) break;
      depth[CLOSE[c]] -= 1;
    }
    i += 1;
  }
  while (i > start && TRAILING.has(text[i - 1])) i -= 1;
  return i;
}

export type Segment = { text: string; url?: string };

/** Plain-text URLs: [start, end, url] in order. */
export function findUrls(text: string): [number, number, string][] {
  const out: [number, number, string][] = [];
  let pos = 0;
  for (;;) {
    START.lastIndex = pos;
    const m = START.exec(text);
    if (!m) break;
    const end = endOfUrl(text, m.index);
    if (end > m.index + m[0].length) out.push([m.index, end, text.slice(m.index, end)]);
    pos = Math.max(end, m.index + m[0].length);
  }
  return out;
}

/** Markdown links: [start, end, label, url], the URL read with balanced parentheses. */
export function markdownLinks(text: string): [number, number, string, string][] {
  const out: [number, number, string, string][] = [];
  const re = /\[([^\]]*)\]\(/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    let i = m.index + m[0].length, depth = 1;
    while (i < text.length && depth && !/\s/.test(text[i])) {
      if (text[i] === "(") depth += 1; else if (text[i] === ")") depth -= 1;
      i += 1;
    }
    if (depth === 0) { out.push([m.index, i, m[1], text.slice(m.index + m[0].length, i - 1)]); re.lastIndex = i; }
  }
  return out;
}

/** Text split into plain parts and links (Markdown links first, then plain URLs). Joining the parts' text gives
 * back the input, except that a Markdown link shows its label. */
export function splitLinks(text: string): Segment[] {
  const parts: Segment[] = [];
  let pos = 0;
  const plain = (s: string) => {
    let p = 0;
    for (const [a, b, url] of findUrls(s)) {
      if (a > p) parts.push({ text: s.slice(p, a) });
      parts.push({ text: url, url });
      p = b;
    }
    if (p < s.length) parts.push({ text: s.slice(p) });
  };
  for (const [a, b, label, url] of markdownLinks(text)) {
    plain(text.slice(pos, a));
    parts.push({ text: label || url, url });
    pos = b;
  }
  plain(text.slice(pos));
  return parts;
}

/** The href for a stored URL: the URL itself, character for character. Only a URL written without a scheme
 * ("github.com/x", "www.x.org") gets "https://" in front, or the browser would read it as a path on this site. */
export function hrefFor(url: string): string {
  const u = url.trim();
  if (/^[a-z][a-z0-9+.-]*:/i.test(u) || u.startsWith("/") || u.startsWith("#")) return u;
  return `https://${u}`;
}
