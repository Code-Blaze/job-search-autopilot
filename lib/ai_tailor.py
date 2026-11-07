#!/usr/bin/env python3
"""AI-rewritten resume for the top N jobs of the day (runs after tailor_resume.py).

Usage: ai_tailor.py <YYYY-MM-DD>
Needs ANTHROPIC_API_KEY (in secrets.env). Without it, this step is skipped and the
rule-based resumes from tailor_resume.py are used for every job.

For each of the first TOP_N rows of output/jobs_<date>.json, Claude rewrites the headline and
the experience/project bullets to mirror the job's wording. Every rewritten line is checked
against config/resume.json: if it contains a number, tool, product, certification or other
proper noun that is not already somewhere in the real resume, that line falls back to the
original. Output: output/resumes/<date>/NN_..._AI.docx; the row's resume_file points at it,
and gaps the model noticed (job asks for X, resume lacks it) go into the row's notes.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import tailor_resume as T  # noqa: E402

TOP_N = int(os.environ.get("AI_TAILOR_TOP_N", "3"))
MODEL = os.environ.get("AI_TAILOR_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AI_TAILOR_EFFORT", "medium")
PRICE_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0)}   # (input, output) USD, for the cost log line

FIELD = (T.C.profile().get("field") or "").strip()
SYSTEM = """You tailor a {FIELD}job seeker's resume to one job posting.

You receive the job posting and the resume's headline and bullet points, each bullet with an id.
Rewrite the headline and each bullet so a recruiter for THIS job immediately sees the relevant
experience: lead with what the job cares about and use the job's own terms where the original
bullet already supports them.

Hard rules - the resume must stay 100% truthful:
- Only rephrase what the original line already says. Never add a tool, technology, platform,
  certification, employer, metric, number, scope, or responsibility that is not in the original
  line or elsewhere in the resume you were given.
- Keep every number exactly as written. Do not inflate (no "led", "owned", "expert" unless the
  original says so).
- Keep each bullet about the same length as the original (one or two lines). Keep past tense
  for past roles.
- If a bullet is not relevant to the job, return it unchanged.
- Headline: at most 12 words, built only from roles/skills already on the resume.
- Return every bullet id exactly once.

Also list up to 4 short "gaps": requirements in the posting that the resume does not show
(for interview preparation; these are NOT added to the resume).""".replace("{FIELD}", FIELD + " " if FIELD else "")

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "bullets": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["id", "text"], "additionalProperties": False}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "bullets", "gaps"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- truthfulness guard
def master_text(master):
    parts = [master["name"]] + list(master["headlines"].values())
    for j in master["experience"]:
        parts += [j["title"], j["org"], j["location"]] + j["bullets"]
    for c in master["skills"]:
        parts += [c["label"]] + c["items"]
    parts += [a["text"] for a in master["achievements"]] + master["certifications"]
    for e in master["education"]:
        parts += [e["school"], e["degree"]] + e["bullets"]
    for p in master["projects"]:
        parts += [p["name"], p["tech"]] + p["bullets"]
    return "\n".join(parts)


NUM = re.compile(r"\d+(?:[.,]\d+)?")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#&./-]*")
# Seniority/ownership claims the original line must already make.
INFLATION = re.compile(r"\b(led|lead|leading|owned|managed|headed|spearheaded|architected|expert|senior|"
                       r"principal|mentored|supervised|single-handedly)\b", re.I)


def unsupported(new, original, vocab_text, vocab_words):
    """Reasons the rewritten line says something the resume doesn't (empty list = OK)."""
    why = []
    for n in NUM.findall(new):
        if n not in original:
            why.append("number %s" % n)
    for name, rx in T.SKILL_RX.items():
        if rx.search(new) and not rx.search(vocab_text):
            why.append("skill %s" % name)
    for name, rx in T.CERT_RX.items():
        if rx.search(new) and not rx.search(vocab_text):
            why.append("cert %s" % name)
    words = WORD.findall(new)
    for i, w in enumerate(words):
        if i == 0 and w[1:].islower():
            continue   # sentence-initial verb like "Monitored"
        for core in re.split(r"[-/]", w.strip(".,;:")):   # "SIEM-driven" -> SIEM, driven
            core = core.strip(".,;:")
            if not core or core.lower() in vocab_words:
                continue
            if any(ch.isupper() for ch in core) or any(ch.isdigit() for ch in core):
                why.append("name %s" % core)
    for m in INFLATION.finditer(new):
        if not re.search(r"\b%s\b" % re.escape(m.group(0)), original, re.I):
            why.append("inflation '%s'" % m.group(0))
    return why


# ---------------------------------------------------------------- model call
def collect_lines(m):
    lines = {}
    for ji, j in enumerate(m["experience"]):
        for bi, b in enumerate(j["bullets"]):
            lines["e%d.%d" % (ji, bi)] = b
    for pi, p in enumerate(m["projects"]):
        for bi, b in enumerate(p["bullets"]):
            lines["p%d.%d" % (pi, bi)] = b
    return lines


def build_prompt(row, m, lines):
    resume = ["HEADLINE: " + m["headline"], ""]
    for ji, j in enumerate(m["experience"]):
        resume.append("ROLE: %s%s (%s)" % (j["org"] + ", " if j["org"] else "", j["title"], j["dates"]))
        resume += ["  [e%d.%d] %s" % (ji, bi, b) for bi, b in enumerate(j["bullets"])]
    for pi, p in enumerate(m["projects"]):
        resume.append("PROJECT: %s (%s)" % (p["name"], p["tech"]))
        resume += ["  [p%d.%d] %s" % (pi, bi, b) for bi, b in enumerate(p["bullets"])]
    resume.append("")
    resume.append("SKILLS (for reference, not to rewrite): " +
                  "; ".join("%s: %s" % (c["label"], ", ".join(c["items"])) for c in m["skills"]))
    resume.append("CERTIFICATIONS (for reference): " + "; ".join(m["certifications"]))
    job = "JOB: %s at %s (%s)\n\n%s" % (row.get("job_title"), row.get("company"),
                                        row.get("location"), row.get("description") or "(no description)")
    return "<job_posting>\n%s\n</job_posting>\n\n<resume>\n%s\n</resume>" % (job, "\n".join(resume))


def call_claude(client, prompt):
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                      # re-run on Anthropic's fallback model if refused
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined (%s)" % getattr(resp.stop_details, "category", None))
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response cut off at max_tokens")
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    return json.loads(text), (u.input_tokens, u.output_tokens), getattr(resp, "_request_id", "")


# ---------------------------------------------------------------- main
def apply_rewrite(m, data, lines, vocab_text, vocab_words, log):
    m = json.loads(json.dumps(m))
    kept = rejected = 0
    new_by_id = {b["id"]: b["text"].strip() for b in data.get("bullets", []) if b.get("id") in lines}
    for key, original in lines.items():
        new = new_by_id.get(key, "")
        bad = unsupported(new, original, vocab_text, vocab_words) if new else ["missing"]
        if bad:
            rejected += 1
            log("    kept original [%s]: %s" % (key, ", ".join(bad[:3])))
            continue
        kind, idx = key[0], key[1:].split(".")
        target = m["experience"] if kind == "e" else m["projects"]
        target[int(idx[0])]["bullets"][int(idx[1])] = new
        kept += 1
    head = (data.get("headline") or "").strip()
    if head and len(head.split()) <= 14 and not unsupported(head, vocab_text, vocab_text, vocab_words):
        m["headline"] = head
    return m, kept, rejected


def main():
    if len(sys.argv) != 2:
        print("usage: ai_tailor.py <YYYY-MM-DD>", file=sys.stderr)
        return 2
    date = sys.argv[1]

    def log(msg):
        print("[ai-tailor] " + msg, flush=True)

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip() or \
            os.environ.get("ANTHROPIC_API_KEY", "").startswith("REPLACE"):
        log("skipped: no ANTHROPIC_API_KEY in secrets.env (rule-based resumes used)")
        return 0
    try:
        import anthropic
    except ImportError:
        log("skipped: Python package 'anthropic' not installed (python3 -m pip install --user anthropic)")
        return 0

    master = T.normalize(T.C.resume())
    jobs_path = os.path.join(ROOT, "output", "jobs_%s.json" % date)
    with open(jobs_path, encoding="utf-8") as f:
        rows = json.load(f)
    vocab_text = master_text(master)
    vocab_words = {w.strip(".,;:/-").lower() for w in WORD.findall(vocab_text)}
    client = anthropic.Anthropic(max_retries=3, timeout=300.0)
    out_dir = os.path.join(ROOT, "output", "resumes", date)
    os.makedirs(out_dir, exist_ok=True)

    total_in = total_out = done = 0
    for i, row in enumerate(rows[:TOP_N], 1):
        name = "%s @ %s" % (row.get("job_title"), row.get("company"))
        if not row.get("description"):
            log("%d. %s: no job description, keeping rule-based resume" % (i, name))
            continue
        prof = T.job_profile(row)
        m = T.tailor(master, prof)
        lines = collect_lines(m)
        try:
            data, (tin, tout), req = call_claude(client, build_prompt(row, m, lines))
        except anthropic.AuthenticationError:
            log("invalid ANTHROPIC_API_KEY - stopping AI step")
            break
        except anthropic.RateLimitError:
            log("rate limited - stopping AI step for today")
            break
        except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError, ValueError,
                StopIteration) as e:
            log("%d. %s: failed (%s), keeping rule-based resume" % (i, name, str(e)[:200]))
            continue
        total_in += tin
        total_out += tout
        log("%d. %s (%d in / %d out tokens, %s)" % (i, name, tin, tout, req))
        m, kept, rejected = apply_rewrite(m, data, lines, vocab_text, vocab_words, log)
        fname = "%02d_%s_%s_AI.docx" % (i, T.slug(row.get("company"), 25), T.slug(row.get("job_title")))
        T.render(m, os.path.join(out_dir, fname))
        row["resume_file"] = os.path.join("resumes", date, fname)
        row["resume_track"] = "AI-" + prof["track"]
        gaps = [g.strip() for g in data.get("gaps", []) if g.strip()][:4]
        if gaps:
            row["notes"] = (row.get("notes", "") + "; gaps: " + " / ".join(gaps)).strip("; ")
        log("    %d lines rewritten, %d kept original -> %s" % (kept, rejected, fname))
        done += 1

    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    pin, pout = PRICE_PER_MTOK.get(MODEL, (0, 0))
    log("done: %d AI resumes, %d in / %d out tokens (~$%.3f)"
        % (done, total_in, total_out, (total_in * pin + total_out * pout) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
