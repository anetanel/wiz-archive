#!/usr/bin/env python3
"""
Build the SQL file for the Cloudflare D1 database behind the REST API
(api/wiz.sql). Run after build.py. deploy.sh imports it into D1.

The search index stores, for every page, its words plus each word with
Hebrew prefix letters (ו ה ב כ ל מ ש) stripped, so a search for "משחק"
also finds "והמשחקים". The same normalisation lives in functions/api/[[path]].js.
"""
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "work")
OUT = os.path.join(ROOT, "api")

PREFIXES = "והבכלמש"
TOKEN = re.compile(r"[0-9a-zא-ת]+")


def norm(s):
    s = s.lower()
    s = re.sub("[֑-ֽֿ-ׇ]", "", s)     # niqqud / cantillation
    s = re.sub("[׳״'\"`´׳״“”„‘’]", "", s)     # geresh, quotes: ד"ר -> דר
    s = s.replace("־", " ")                          # maqaf
    return s


def index_tokens(text):
    out = []
    for t in TOKEN.findall(norm(text)):
        out.append(t)
        if "א" <= t[0] <= "ת":
            v = t
            for _ in range(3):
                if len(v) > 3 and v[0] in PREFIXES:
                    v = v[1:]
                    out.append(v)
                else:
                    break
    return " ".join(out)


def issue_hash(statements):
    return hashlib.sha256("\n".join(statements).encode()).hexdigest()


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def main():
    with open(os.path.join(WORK, "issues.json"), encoding="utf-8") as fh:
        issues = json.load(fh)
    os.makedirs(OUT, exist_ok=True)
    lines = [
        "DROP TABLE IF EXISTS pages_fts;",
        "DROP TABLE IF EXISTS pages;",
        "DROP TABLE IF EXISTS toc;",
        "DROP TABLE IF EXISTS issues;",
        "CREATE TABLE issues (num INTEGER PRIMARY KEY, year INTEGER, month INTEGER, "
        "date_estimated INTEGER, notes TEXT, page_count INTEGER, has_pdf INTEGER);",
        "CREATE TABLE pages (id INTEGER PRIMARY KEY, issue INTEGER, page INTEGER, "
        "pdf_page INTEGER, text TEXT);",
        "CREATE INDEX pages_issue ON pages(issue, page);",
        "CREATE TABLE toc (issue INTEGER, page INTEGER, page_end INTEGER, section TEXT, "
        "title TEXT, norm TEXT);",
        "CREATE INDEX toc_issue ON toc(issue, page);",
        "CREATE VIRTUAL TABLE pages_fts USING fts5(tokens, content='', "
        "tokenize='unicode61 remove_diacritics 0');",
    ]
    n_pages = 0
    per_issue = {}          # num -> list of INSERT statements, in a fixed order
    fts = {}                # num -> {rowid: tokens}  (needed later to delete them)
    for i in issues:
        num = i["num"]
        tpath = os.path.join(WORK, "text", i["id"] + ".json")
        texts = {}
        if os.path.exists(tpath):
            with open(tpath, encoding="utf-8") as fh:
                texts = {int(k): v for k, v in json.load(fh).items()}
        st = per_issue[num] = []
        fts[num] = {}
        st.append("INSERT INTO issues VALUES (%s);" % ",".join(map(q, [
            num, i.get("year"), i.get("month"), bool(i.get("date_estimated")),
            i.get("notes"), len(i["pages"]), bool(i.get("pdf"))])))
        for page, pdf_page in i["pages"]:
            rid = num * 1000 + page
            text = texts.get(page, "")
            toks = index_tokens(text)
            st.append("INSERT INTO pages VALUES (%s);" % ",".join(map(q, [
                rid, num, page, pdf_page, text])))
            st.append("INSERT INTO pages_fts(rowid, tokens) VALUES (%d, %s);" % (rid, q(toks)))
            fts[num][str(rid)] = toks
            n_pages += 1
        for e in i.get("toc", []):
            st.append("INSERT INTO toc VALUES (%s);" % ",".join(map(q, [
                num, e["page"], e.get("page_end"), e.get("section"), e["title"],
                norm(" ".join(filter(None, [e.get("section"), e["title"]])))])))
    lines += [l for n in sorted(per_issue) for l in per_issue[n]]

    # per-issue state, so deploy can update only the issues that changed
    idir = os.path.join(OUT, "issues")
    os.makedirs(idir, exist_ok=True)
    for f in os.listdir(idir):
        os.remove(os.path.join(idir, f))
    for num, st in per_issue.items():
        with open(os.path.join(idir, "%03d.json" % num), "w", encoding="utf-8") as fh:
            json.dump({"hash": issue_hash(st), "statements": st, "fts": fts[num]}, fh, ensure_ascii=False)

    # schema (incl. the FTS virtual table) is run as a command, data imported as a file
    schema = [l for l in lines if not l.startswith("INSERT")]
    data = [l for l in lines if l.startswith("INSERT")]
    with open(os.path.join(OUT, "schema.sql"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(schema) + "\n")
    sql = "\n".join(data) + "\n"
    path = os.path.join(OUT, "data.sql")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(sql)
    with open(os.path.join(OUT, "wiz.sql"), "w", encoding="utf-8") as fh:   # both, for local tests
        fh.write("\n".join(schema) + "\n" + sql)
    with open(os.path.join(OUT, "schema.sha256"), "w") as fh:
        fh.write(hashlib.sha256("\n".join(schema).encode()).hexdigest())
    longest = max(len(l.encode()) for l in lines)
    print(f"api/data.sql: {len(issues)} issues, {n_pages} pages, "
          f"{len(sql.encode()) / 1024**2:.1f} MB, longest statement {longest / 1024:.0f} KB")
    if longest > 100 * 1024:
        print("!! A statement is over D1's 100 KB limit")


if __name__ == "__main__":
    main()
