#!/usr/bin/env python3
"""Make a tailored .docx resume for every job in output/jobs_<date>.json (rule-based, no AI).

Usage: tailor_resume.py <YYYY-MM-DD>
Reads config/resume.json, writes output/resumes/<date>/NN_<Company>_<Title>.docx and adds
"resume_file" / "resume_track" to each row of output/jobs_<date>.json (the tracker links them).

Tailoring ONLY reorders true content from resume.json — it never adds or rewrites text:
  * headline: the variant for the job's "track" (profile.json resume_tracks), by what the job asks for
  * skills: the category matching the job's track first; skills the job mentions first in each line
  * bullets within each job / project: ones that mention the job's skills first
  * projects and certifications: most relevant first (experience stays chronological)
A check compares every tailored resume with resume.json; if any wording differs, that
resume is not written.
"""
import json
import os
import re
import sys
import zipfile
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import config as C  # noqa: E402
from classify_from_raw import CERTS, SKILLS  # noqa: E402  (same skill/cert regexes as the tracker)

SKILL_RX = dict(SKILLS)
CERT_RX = dict(CERTS)
# Resume "tracks" (profile.json resume_tracks): each has title keywords and the skill names
# (from profile.json skills) that point to it. Earlier tracks win ties.
TRACKS = [{"id": t["id"], "title": C.keywords_rx(t.get("title_keywords")),
           "skills": set(t.get("skills", []))} for t in C.profile().get("resume_tracks", [])]
RES = C.profile().get("resume", {})


# ---------------------------------------------------------------- tailoring
def normalize(m):
    """Fill in optional parts of resume.json so a short resume still works."""
    m = json.loads(json.dumps(m))
    m.setdefault("headlines", {}).setdefault("default", m.get("headline", ""))
    for k in ("contact", "experience", "skills", "achievements", "education", "certifications", "projects"):
        m[k] = m.get(k) or []
    m["contact"] = [c if isinstance(c, dict) else {"text": c} for c in m["contact"]]
    m["achievements"] = [a if isinstance(a, dict) else {"text": a} for a in m["achievements"]]
    for j in m["experience"]:
        for k in ("title", "org", "location", "dates"):
            j[k] = j.get(k) or ""
        j["bullets"] = j.get("bullets") or []
    for c in m["skills"]:
        c["label"] = c.get("label") or ""
        c["items"] = c.get("items") or []
    for e in m["education"]:
        for k in ("school", "dates", "degree"):
            e[k] = e.get(k) or ""
        e["bullets"] = e.get("bullets") or []
    for p in m["projects"]:
        p["name"] = p.get("name") or ""
        p["tech"] = p.get("tech") or ""
        p["bullets"] = p.get("bullets") or []
    return m


def job_profile(row):
    skills = [s.strip() for s in (row.get("skills") or "").split(",") if s.strip() in SKILL_RX]
    certs = [c.strip() for c in (row.get("certs") or "").split(",") if c.strip() in CERT_RX]
    title = row.get("job_title", "")
    track, best, track_set = "default", 0, set()
    for t in TRACKS:
        sc = 3 * bool(t["title"].search(title)) + sum(s in t["skills"] for s in skills)
        if sc > best:
            track, best, track_set = t["id"], sc, t["skills"]
    return {"skills": skills, "certs": certs, "track": track, "track_skills": track_set}


def score(text, prof):
    hits = sum(1 for s in prof["skills"] if SKILL_RX[s].search(text))
    bonus = sum(0.25 for s in prof["track_skills"] if s in SKILL_RX and SKILL_RX[s].search(text))
    return hits + bonus


def by_score(items, key, prof):
    """Stable sort, most relevant first."""
    return [x for _, x in sorted(enumerate(items), key=lambda p: (-score(key(p[1]), prof), p[0]))]


def tailor(master, prof):
    m = json.loads(json.dumps(master))  # deep copy
    track = prof["track"]
    m["headline"] = m["headlines"].get(track, m["headlines"]["default"])
    for job in m["experience"]:
        job["bullets"] = by_score(job["bullets"], lambda b: b, prof)
    for proj in m["projects"]:
        proj["bullets"] = by_score(proj["bullets"], lambda b: b, prof)
    m["projects"] = by_score(m["projects"], lambda p: p["name"] + " " + p["tech"] + " " + " ".join(p["bullets"]), prof)
    for cat in m["skills"]:
        cat["items"] = by_score(cat["items"], lambda s: s, prof)
    m["skills"] = [x for _, x in sorted(enumerate(m["skills"]), key=lambda p: (
        p[1].get("track") != track, -score(" ".join(p[1]["items"]), prof), p[0]))]
    wanted = [CERT_RX[c] for c in prof["certs"]]
    m["certifications"] = [x for _, x in sorted(enumerate(m["certifications"]), key=lambda p: (
        not any(rx.search(p[1]) for rx in wanted), p[0]))]
    order = m.get("section_order") or {}
    m["sections"] = order.get(track) or order.get("default") or DEFAULT_SECTIONS
    return m


def content_fingerprint(m):
    """Every piece of resume text, order-independent (headline excluded: chosen from master)."""
    parts = [m["name"]] + [c["text"] for c in m["contact"]]
    for j in m["experience"]:
        parts += [j["title"], j["org"], j["location"], j["dates"]] + j["bullets"]
    for c in m["skills"]:
        parts += [c["label"]] + c["items"]
    parts += [a["text"] for a in m["achievements"]]
    for e in m["education"]:
        parts += [e["school"], e["dates"], e["degree"]] + e["bullets"]
    parts += m["certifications"]
    for p in m["projects"]:
        parts += [p["name"], p["tech"]] + p["bullets"]
    return Counter(parts)


# ---------------------------------------------------------------- docx (hand-written OOXML)
DEFAULT_SECTIONS = ["experience", "skills", "projects", "certifications", "achievements", "education"]
FONT = RES.get("font", "Times New Roman")
LANG = RES.get("language", "en-US")
PAGE_W, PAGE_H = (12240, 15840) if str(RES.get("page_size", "A4")).lower() == "letter" else (11906, 16838)
MARGIN = 720                                          # 0.5" margins (twips)
TEXT_W = PAGE_W - 2 * MARGIN


def x(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class Doc:
    def __init__(self):
        self.body, self.links = [], []

    def link_id(self, url):
        self.links.append(url)
        return "rIdL%d" % len(self.links)

    @staticmethod
    def run(text, size=21, bold=False, italic=False, color=None, underline=False):
        rpr = '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/>' % (FONT, FONT, FONT)
        rpr += "<w:b/>" if bold else ""
        rpr += "<w:i/>" if italic else ""
        rpr += '<w:color w:val="%s"/>' % color if color else ""
        rpr += '<w:u w:val="single"/>' if underline else ""
        rpr += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size)
        return '<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, x(text))

    def link(self, text, url, **kw):
        return '<w:hyperlink r:id="%s">%s</w:hyperlink>' % (self.link_id(url), self.run(text, **kw))

    def para(self, runs, align=None, before=0, after=0, border=False, bullet=False, keep=False):
        ppr = ""
        if keep:
            ppr += "<w:keepNext/>"
        if bullet:
            ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        if border:
            ppr += '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="000000"/></w:pBdr>'
        ppr += '<w:spacing w:before="%d" w:after="%d" w:line="245" w:lineRule="auto"/>' % (before, after)
        if align:
            ppr += '<w:jc w:val="%s"/>' % align
        self.body.append("<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (ppr, "".join(runs)))

    def tab_line(self, left_runs, right_runs, before=0, rw=2600):
        """Left text + right-aligned text on one line: a borderless 2-column table
        (renders the same in Word, Google Docs, Pages and previewers, unlike tab stops)."""
        lw = TEXT_W - rw

        def cell(w, runs, align):
            return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr><w:p><w:pPr><w:keepNext/>'
                    '<w:spacing w:before="%d" w:after="0"/><w:jc w:val="%s"/></w:pPr>%s</w:p></w:tc>'
                    % (w, before, align, "".join(runs)))
        nb = "".join('<w:%s w:val="nil"/>' % e for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
        self.body.append(
            '<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            '<w:tblBorders>%s</w:tblBorders><w:tblCellMar><w:left w:w="0" w:type="dxa"/>'
            '<w:right w:w="0" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
            '<w:gridCol w:w="%d"/><w:gridCol w:w="%d"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>%s%s</w:tr></w:tbl>'
            % (TEXT_W, nb, lw, rw, cell(lw, left_runs, "left"), cell(rw, right_runs, "right")))

    def heading(self, text):
        self.para([self.run(text, size=25, bold=True)], before=140, after=60, border=True, keep=True)

    def bullet(self, runs):
        self.para(runs, bullet=True, after=10)


def render(m, path):
    d = Doc()
    d.para([d.run(m["name"], size=40, bold=True)], align="center", after=20)
    d.para([d.run(m["headline"], size=23)], align="center", after=40)
    lines = [ln for ln in (m["contact"][:4], m["contact"][4:]) if ln]   # up to 4 per line
    for k, line in enumerate(lines):
        contact = []
        for i, c in enumerate(line):
            if i:
                contact.append(d.run("  |  ", size=19))
            contact.append(d.link(c["text"], c["url"], size=19) if c.get("url") else d.run(c["text"], size=19))
        d.para(contact, align="center", after=40 if k == len(lines) - 1 else 0)

    def experience():
        d.heading("Experience")
        for j in m["experience"]:
            left = [d.run(j["org"] + ", " if j["org"] else "", bold=True), d.run(j["title"], bold=True)]
            if j["location"]:
                left.append(d.run(" | " + j["location"]))
            d.tab_line(left, [d.run(j["dates"])])
            for b in j["bullets"]:
                d.bullet([d.run(b)])

    def skills():
        d.heading("Skills")
        for c in m["skills"]:
            d.bullet([d.run(c["label"] + ": ", bold=True), d.run(", ".join(c["items"]))])

    def achievements():
        d.heading("Achievements")
        for a in m["achievements"]:
            lt, url = a.get("link_text"), a.get("url")
            if lt and url and lt in a["text"]:
                pre, post = a["text"].split(lt, 1)
                d.bullet([d.run(pre), d.link(lt, url, color="1F4E9D"), d.run(post)])
            else:
                d.bullet([d.run(a["text"])])

    def education():
        d.heading("Education")
        for e in m["education"]:
            d.tab_line([d.run(e["school"], bold=True)], [d.run(e["dates"])])
            d.para([d.run(e["degree"], italic=True)], after=10, keep=True)
            for b in e["bullets"]:
                d.bullet([d.run(b)])

    def certifications():
        d.heading("Certifications")
        for c in m["certifications"]:
            d.bullet([d.run(c)])

    def projects():
        d.heading("Projects")
        for p in m["projects"]:
            name = d.link(p["name"], p["url"], bold=True) if p.get("url") else d.run(p["name"], bold=True)
            d.tab_line([name], [d.run(p["tech"], italic=True, size=19)], before=60, rw=4000)
            for b in p["bullets"]:
                d.bullet([d.run(b)])

    sections = {"experience": experience, "skills": skills, "achievements": achievements,
                "education": education, "certifications": certifications, "projects": projects}
    for s in m["sections"]:
        if s in sections and m.get(s):          # skip sections the resume doesn't have
            sections[s]()
    write_docx(d, path, m["name"])


NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def write_docx(d, path, author):
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>%s'
                '<w:sectPr><w:pgSz w:w="%d" w:h="%d"/><w:pgMar w:top="%d" w:right="%d" w:bottom="%d" '
                'w:left="%d" w:header="360" w:footer="360" w:gutter="0"/></w:sectPr></w:body></w:document>'
                % (NS, "".join(d.body), PAGE_W, PAGE_H, MARGIN, MARGIN, MARGIN, MARGIN))
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles %s><w:docDefaults>'
              '<w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/><w:sz w:val="21"/>'
              '<w:lang w:val="%s"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="0"/>'
              '</w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" '
              'w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="character" '
              'w:styleId="Hyperlink"><w:name w:val="Hyperlink"/></w:style></w:styles>' % (NS, FONT, FONT, FONT, LANG))
    numbering = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering %s>'
                 '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
                 '<w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
                 '<w:pPr><w:ind w:left="360" w:hanging="220"/></w:pPr><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s"/>'
                 '</w:rPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
                 '</w:numbering>' % (NS, FONT, FONT))
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rIdN" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>']
    for i, url in enumerate(d.links, 1):
        rels.append('<Relationship Id="rIdL%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/hyperlink" Target="%s" TargetMode="External"/>' % (i, x(url)))
    rels.append("</Relationships>")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>')
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s Resume</dc:title><dc:creator>%s</dc:creator>'
            '</cp:coreProperties>' % (x(author), x(author)))
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/numbering.xml", numbering)
        z.writestr("word/_rels/document.xml.rels", "".join(rels))
    os.replace(tmp, path)


# ---------------------------------------------------------------- main
def slug(s, n=40):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:n] or "x"


def main():
    if len(sys.argv) != 2:
        print("usage: tailor_resume.py <YYYY-MM-DD>", file=sys.stderr)
        return 2
    date = sys.argv[1]
    master = normalize(C.resume())
    jobs_path = os.path.join(ROOT, "output", "jobs_%s.json" % date)
    with open(jobs_path, encoding="utf-8") as f:
        rows = json.load(f)
    rel_dir = os.path.join("resumes", date)
    out_dir = os.path.join(ROOT, "output", rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    want = content_fingerprint(master)
    tracks, bad = Counter(), 0
    for i, row in enumerate(rows, 1):
        prof = job_profile(row)
        m = tailor(master, prof)
        if content_fingerprint(m) != want:
            print("[tailor] content check FAILED for %s — skipped" % row.get("job_title"))
            bad += 1
            continue
        name = "%02d_%s_%s.docx" % (i, slug(row.get("company"), 25), slug(row.get("job_title")))
        render(m, os.path.join(out_dir, name))
        row["resume_file"] = os.path.join(rel_dir, name)
        row["resume_track"] = prof["track"]
        tracks[prof["track"]] += 1
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("[tailor] %d resumes -> output/%s (tracks: %s)%s"
          % (sum(tracks.values()), rel_dir, dict(tracks), "; %d FAILED check" % bad if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
