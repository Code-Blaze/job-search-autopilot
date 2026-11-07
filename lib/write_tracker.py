#!/usr/bin/env python3
"""Write output/jobs_<date>.json to an .xlsx (stdlib zipfile + hand-written OOXML).

Usage: write_tracker.py output/jobs_<date>.json
Writes output/<Your_Name>_Job_Tracker_<date>.xlsx and output/.index_<date>.json.
"""
import glob
import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import config as C  # noqa: E402

# (header, row key, width)
COLUMNS = [
    ("Status", "status", 12), ("Posted Recency", "posted_recency", 15),
    ("Work Arrangement", "work_arrangement", 17), ("Seen Before", "seen_before", 11),
    ("Job Title", "job_title", 42), ("Company", "company", 28), ("Location", "location", 26),
    ("Employment Type", "employment_type", 15), ("Seniority", "seniority", 12),
    ("Posted Date", "posted_date", 12), ("Skill Match", "skill_match", 11), ("Source", "source", 10),
    ("Skills", "skills", 45), ("Certs Mentioned", "certs", 20), ("Company Size", "company_size", 14),
    ("Company Industry", "company_industry", 24), ("Apply Link", "apply_link", 14), ("Tailored Resume", "resume_file", 44),
    ("Salary", "salary", 22), ("Salary (est/yr)", "salary_est", 15), ("Notes", "notes", 34),
]
LINK_COL = [c[1] for c in COLUMNS].index("apply_link")
RESUME_COL = [c[1] for c in COLUMNS].index("resume_file")
WA_COL = [c[1] for c in COLUMNS].index("work_arrangement")
WA_STYLE = {"Remote": 3, "Hybrid": 4, "Onsite": 5, "Not Specified": 6}
STATUS_COL = [c[1] for c in COLUMNS].index("status")
STATUS_OPTIONS = ["Not Applied", "Applied", "Interviewing", "Rejected", "Offer"]
# status -> dxf index in STYLES (Status-cell highlight; cellIs rules so Numbers keeps them)
STATUS_DXF = {"Applied": 0, "Interviewing": 1, "Offer": 2, "Rejected": 3}

CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def esc(v):
    v = CTRL.sub("", str(v))
    return (v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def cell(ref, value, style=0):
    st = ' s="%d"' % style if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, st, value)
    if value in (None, ""):
        return '<c r="%s"%s/>' % (ref, st) if style else ""
    # Scraped text can arrive HTML-encoded ("McCormick &amp; Company"); decode so it isn't
    # escaped twice and shown as "&amp;". Cell text only: never URLs ("&copy=" would break).
    text = esc(html.unescape(str(value)))[:32000]
    return '<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, text)


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3">
 <font><sz val="11"/><name val="Calibri"/></font>
 <font><b/><sz val="11"/><name val="Calibri"/></font>
 <font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font>
</fonts>
<fills count="7">
 <fill><patternFill patternType="none"/></fill>
 <fill><patternFill patternType="gray125"/></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFFFEB9C"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFFCE4D6"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="7">
 <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
 <xf numFmtId="0" fontId="1" fillId="6" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
 <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
 <xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>
 <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
 <xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>
 <xf numFmtId="0" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="4">
 <dxf><fill><patternFill patternType="solid"><bgColor rgb="FFC6EFCE"/></patternFill></fill></dxf>
 <dxf><fill><patternFill patternType="solid"><bgColor rgb="FFFFEB9C"/></patternFill></fill></dxf>
 <dxf><font><b/></font><fill><patternFill patternType="solid"><bgColor rgb="FF92D050"/></patternFill></fill></dxf>
 <dxf><font><strike/><color rgb="FF808080"/></font></dxf>
</dxfs>
</styleSheet>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Jobs" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def previous_index(date):
    """Most recent .index_<d>.json with d < date."""
    best = None
    for p in glob.glob(os.path.join(ROOT, "output", ".index_*.json")):
        d = os.path.basename(p)[len(".index_"):-len(".json")]
        if d < date and (best is None or d > best[0]):
            best = (d, p)
    if not best:
        return set()
    try:
        with open(best[1], encoding="utf-8") as f:
            return set(json.load(f).get("keys", []))
    except (ValueError, OSError):
        return set()


def build_sheet(rows):
    out, links = [], []
    ncol = len(COLUMNS)
    last = col_letter(ncol - 1)
    out.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">')
    out.append('<dimension ref="A1:%s%d"/>' % (last, len(rows) + 1))
    out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
               'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    out.append("<cols>" + "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w)
                                  for i, (_, _, w) in enumerate(COLUMNS)) + "</cols>")
    out.append("<sheetData>")
    out.append('<row r="1">' + "".join(cell("%s1" % col_letter(i), h, 1)
                                        for i, (h, _, _) in enumerate(COLUMNS)) + "</row>")
    for n, row in enumerate(rows, start=2):
        cells = []
        for i, (_, key, _) in enumerate(COLUMNS):
            ref = "%s%d" % (col_letter(i), n)
            v = row.get(key, "")
            if i == LINK_COL and v:
                links.append((ref, v))
                cells.append(cell(ref, "Apply", 2))
            elif i == RESUME_COL and v:
                # Absolute file:// URL: a relative link breaks once the .xlsx is opened
                # from anywhere but output/ (e.g. the copy downloaded from Telegram).
                path = v if os.path.isabs(v) else os.path.join(ROOT, "output", v)
                # Apple Numbers won't follow file:// links, so the cell shows the file name
                # (matches output/resumes/<date>/ and the Telegram zip); Excel can still click it.
                links.append((ref, pathlib.Path(os.path.abspath(path)).as_uri()))   # works on Windows too
                cells.append(cell(ref, os.path.basename(v), 2))
            elif i == WA_COL:
                cells.append(cell(ref, v or "Not Specified", WA_STYLE.get(v or "Not Specified", 0)))
            else:
                cells.append(cell(ref, v))
        out.append('<row r="%d">%s</row>' % (n, "".join(cells)))
    out.append("</sheetData>")
    out.append('<autoFilter ref="A1:%s%d"/>' % (last, len(rows) + 1))
    # Status column: colour the Status cell by its value, and a click-to-pick dropdown.
    # Plain "cell value equals" rules, not formula rules: Apple Numbers drops formula-based
    # conditional formatting (with a warning) but keeps these.
    # Covers 500 extra rows so rows you add by hand get it too.
    end = len(rows) + 501
    sc = col_letter(STATUS_COL)
    out.append('<conditionalFormatting sqref="%s2:%s%d">' % (sc, sc, end) + "".join(
        '<cfRule type="cellIs" dxfId="%d" priority="%d" operator="equal"><formula>"%s"</formula></cfRule>'
        % (dxf, p + 1, name) for p, (name, dxf) in enumerate(STATUS_DXF.items()))
        + "</conditionalFormatting>")
    out.append('<dataValidations count="1"><dataValidation type="list" allowBlank="1" '
               'showInputMessage="1" showErrorMessage="1" promptTitle="Status" '
               'prompt="Pick Applied once you have applied" sqref="%s2:%s%d">'
               '<formula1>"%s"</formula1></dataValidation></dataValidations>'
               % (sc, sc, end, ",".join(STATUS_OPTIONS)))
    if links:
        out.append("<hyperlinks>" + "".join('<hyperlink ref="%s" r:id="rId%d"/>' % (ref, k + 1)
                                            for k, (ref, _) in enumerate(links)) + "</hyperlinks>")
    out.append("</worksheet>")
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for k, (_, url) in enumerate(links):
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/hyperlink" Target="%s" TargetMode="External"/>' % (k + 1, esc(url)))
    rels.append("</Relationships>")
    return "".join(out), "".join(rels)


def main():
    if len(sys.argv) != 2:
        print("usage: write_tracker.py output/jobs_<date>.json", file=sys.stderr)
        return 2
    src = sys.argv[1]
    m = re.search(r"jobs_(\d{4}-\d{2}-\d{2})\.json$", src)
    if not m:
        print("[write] cannot read date from %s" % src)
        return 2
    date = m.group(1)
    with open(src, encoding="utf-8") as f:
        rows = json.load(f)

    seen = previous_index(date)
    keys = []
    for r in rows:
        k = r.get("_key", "")
        keys.append(k)
        r["seen_before"] = "Y" if k in seen else "N"
    new = sum(1 for r in rows if r["seen_before"] == "N")

    sheet, sheet_rels = build_sheet(rows)
    out = C.tracker_path(date)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", sheet_rels)
    os.replace(tmp, out)

    with open(os.path.join(ROOT, "output", ".index_%s.json" % date), "w", encoding="utf-8") as f:
        json.dump({"count": len(rows), "new_since_last": new, "keys": keys}, f)
    print("[write] %d rows (%d new since last run) -> %s" % (len(rows), new, os.path.relpath(out, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
