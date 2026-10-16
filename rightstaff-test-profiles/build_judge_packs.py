"""Build paste-ready judge packs from the profile files.

Usage: python build_judge_packs.py [path/to/baselines.json]
Writes judge_packs/*.txt. Names, GitHub usernames and design notes never reach the packs.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).parent
BASELINES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "rightstaff-eeat-mock" / "seed" / "baselines.json"

ROLES = {
    "data-scientist":  {"baseline": "baseline-data-scientist-entry",  "labels": {"A": "DS2", "B": "DS4", "C": "DS1", "D": "DS5", "E": "DS3"}},
    "ai-engineer":     {"baseline": "baseline-ai-engineer-entry",     "labels": {"A": "AI3", "B": "AI1", "C": "AI5", "D": "AI2", "E": "AI4"}},
    "product-manager": {"baseline": "baseline-product-manager-entry", "labels": {"A": "PM4", "B": "PM2", "C": "PM3", "D": "PM1", "E": "PM5"}},
}

PROMPT = """You are an experienced hiring manager filling the entry-level role described below. You will see the application materials of five candidates (resume and, where provided, LinkedIn profile, GitHub, portfolio, certifications and references). Names have been removed; candidates are labelled A to E. The order of the candidates means nothing.

Task: rank the candidates by whom you would most want to interview for this role.
- Judge what each candidate has actually done and can show that is relevant to this role.
- Do not give credit for length, formatting or wording style on their own.
- If two candidates are genuinely equal, you may tie them with "=".
- Use only the information given. Do not search the web.

Answer in exactly this format:

RANKING: X > X > X > X > X
GAPS: X-X clear|slight, X-X clear|slight, X-X clear|slight, X-X clear|slight
DECIDING FACTOR: X-X experience|skills|results|proof|other, (one per adjacent pair, same order as GAPS)
REASONS:
A: one sentence
B: one sentence
C: one sentence
D: one sentence
E: one sentence
"""


def parse_profile(path: Path) -> dict:
    text = path.read_text()
    head, _, body = text.partition("\n## ")
    meta = dict(re.findall(r"^([\w-]+):\s*(.+)$", head, re.M))
    sections = {}
    for chunk in ("## " + body).split("\n## ")[0:]:
        chunk = chunk.removeprefix("## ")
        title, _, content = chunk.partition("\n")
        sections[title.strip()] = content.strip()
    return {"meta": meta, "sections": sections}


def json_block(s: str):
    m = re.search(r"```json\s*(.+?)```", s, re.S)
    return json.loads(m.group(1)) if m else None


def strip_identity(text: str, meta: dict, label: str) -> str:
    name = meta["Name"]
    repl = {name: f"Candidate {label}"}
    for part in name.split():
        repl[part] = "the candidate"
    if meta.get("GitHub-username", "none") != "none":
        repl[meta["GitHub-username"]] = "candidate"
    for t in meta.get("Strip", "").split(","):
        if t.strip():
            repl[t.strip()] = "candidate"
    for t in sorted(repl, key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(t) + r"\b", repl[t], text, flags=re.I)
    return text


def render_candidate(p: dict, label: str) -> str:
    s = p["sections"]
    out = [f"=== CANDIDATE {label} ===", "", "--- Resume ---", s["Resume"], ""]
    if s["LinkedIn"] != "None provided.":
        out += ["--- LinkedIn profile ---", s["LinkedIn"], ""]
    gh = json_block(s["GitHub"])
    if gh:
        out.append("--- GitHub (public repositories) ---")
        for r in gh["repos"]:
            fork = "fork of another project" if r["is_fork"] else "own repository"
            out.append(f"- {r['name']}: {r['description']} ({', '.join(r['languages'])}; {fork}; commits by candidate: {r['own_commits']}; last updated {r['last_push']})")
            if r.get("readme"):
                out.append(f"  README: {r['readme']}")
        out.append("")
    if s["Portfolio"] != "None provided.":
        out += ["--- Portfolio ---", s["Portfolio"], ""]
    certs = json_block(s["Certifications (form)"])
    if certs:
        out.append("--- Certifications submitted ---")
        for c in certs:
            cid = "credential ID provided" if c.get("credential_id") else "no credential ID"
            out.append(f"- {c['name']} ({c.get('issuer') or 'issuer not stated'}, {c.get('date') or 'date not stated'}; {cid})")
        out.append("")
    refs = json_block(s["References (form)"])
    if refs:
        out.append("--- References submitted ---")
        for r in refs:
            out.append(f"- Relationship: {r['relationship']}. Statement: \"{r['statement']}\"")
        out.append("")
    if len(out) == 5:
        out += ["(No LinkedIn, GitHub, portfolio, certifications or references provided.)", ""]
    return strip_identity("\n".join(out), p["meta"], label)


def role_text(b: dict) -> str:
    req = [c["name"] for c in b["criteria"] if c["priority"] == "required" and c["kind"] != "certification"]
    pref = [c["name"] for c in b["criteria"] if c["priority"] == "preferred"]
    return "\n".join([
        f"=== THE ROLE: {b['title']} (entry level, about {b['required_years']} year of experience expected) ===",
        f"About the role: {b['description']}",
        f"What you will do: {b['scope_expectation']}",
        f"Required: {', '.join(req)}",
        f"Nice to have: {', '.join(pref)}",
        "",
    ])


def main():
    baselines = {b["id"]: b for b in json.loads(BASELINES.read_text())["baselines"]}
    outdir = ROOT / "judge_packs"
    outdir.mkdir(exist_ok=True)
    n = 0
    for role, cfg in ROLES.items():
        b = baselines[cfg["baseline"]]
        profs = {pid: parse_profile(ROOT / "profiles" / role / f"{pid}.md") for pid in cfg["labels"].values()}
        variants = [("forward", "ABCDE", {}), ("reversed", "EDCBA", {})]
        if role == "data-scientist":
            variants.append(("lengthcheck", "ABCDE", {"B": parse_profile(ROOT / "profiles" / role / "DS4_padded.md")}))
        for name, order, swap in variants:
            parts = [PROMPT, role_text(b)]
            for lab in order:
                p = swap.get(lab) or profs[cfg["labels"][lab]]
                parts.append(render_candidate(p, lab))
            parts.append("Now give your answer in the required format.")
            n += 1
            (outdir / f"{n:02d}_{role}_{name}.txt").write_text("\n".join(parts))
    print(f"wrote {n} packs to {outdir}")


if __name__ == "__main__":
    main()
