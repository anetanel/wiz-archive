#!/usr/bin/env python3
"""
Keep the D1 database in step with api/ while writing as few rows as possible
(the free plan allows 100,000 written rows a day; a full import is ~30,000).

    api_sync.py legacy        adopt the state of an older full import (once)
    api_sync.py plan          -> prints "none", "update <rows>" or "full <rows>"
                                 and writes api/update.sql for "update"
    api_sync.py commit MODE   record that the plan was applied (MODE: update|full)

api/imported/NNN.json holds what the database currently contains for each
issue (its hash and its search-index tokens, which are needed to delete the
old search entries of an issue that changed).
"""
import hashlib
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(ROOT, "api")
NEW = os.path.join(API, "issues")
CUR = os.path.join(API, "imported")
SCHEMA_DONE = os.path.join(API, "imported-schema.sha256")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_dir(d):
    out = {}
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    out[int(f[:-5])] = json.load(fh)
    return out


def q(s):
    return "'" + s.replace("'", "''") + "'"


def legacy():
    """Older deploy.command did full imports and kept only api/imported.sha256.
    If the api/*.sql files still match it, split them into per-issue state."""
    old = os.path.join(API, "imported.sha256")
    if os.path.isdir(CUR) or not os.path.exists(old):
        return
    try:
        schema, data = read(os.path.join(API, "schema.sql")), read(os.path.join(API, "data.sql"))
    except FileNotFoundError:
        return
    schema_lines = schema.rstrip("\n")
    if hashlib.sha256((schema_lines + data).encode()).hexdigest() != read(old).strip():
        print("legacy: database state unknown – a full import will be needed")
        return
    per, fts = {}, {}
    # statements were joined with "\n"; page text itself can contain newlines
    statements = ["INSERT INTO " + x for x in ("\n" + data.rstrip("\n")).split("\nINSERT INTO ")[1:]]
    for line in statements:
        m = re.match(r"INSERT INTO (\w+)(\(rowid, tokens\))? VALUES \((\d+),", line)
        if not m:
            continue
        table, first = m.group(1), int(m.group(3))
        num = first // 1000 if table in ("pages", "pages_fts") else first
        per.setdefault(num, []).append(line)
        if table == "pages_fts":
            tm = re.match(r"INSERT INTO pages_fts\(rowid, tokens\) VALUES \(\d+, '(.*)'\);$", line, re.S)
            fts.setdefault(num, {})[str(first)] = tm.group(1).replace("''", "'")
    os.makedirs(CUR)
    for num, st in per.items():
        with open(os.path.join(CUR, "%03d.json" % num), "w", encoding="utf-8") as fh:
            json.dump({"hash": hashlib.sha256("\n".join(st).encode()).hexdigest(),
                       "fts": fts.get(num, {})}, fh, ensure_ascii=False)
    with open(SCHEMA_DONE, "w") as fh:
        fh.write(hashlib.sha256(schema_lines.encode()).hexdigest())
    print(f"legacy: adopted state of the last full import ({len(per)} issues)")


def plan():
    new, cur = load_dir(NEW), load_dir(CUR)
    schema_now = read(os.path.join(API, "schema.sha256")).strip()
    schema_db = read(SCHEMA_DONE).strip() if os.path.exists(SCHEMA_DONE) else None
    full_rows = sum(len(v["statements"]) + len(v["fts"]) for v in new.values())
    if schema_db != schema_now or not cur:
        print(f"full {full_rows}")
        return
    changed = [n for n in new if n not in cur or cur[n]["hash"] != new[n]["hash"]]
    removed = [n for n in cur if n not in new]
    if not changed and not removed:
        print("none")
        return
    out, rows = [], 0
    for n in sorted(set(changed) | set(removed)):
        out += [f"DELETE FROM issues WHERE num = {n};",
                f"DELETE FROM pages WHERE issue = {n};",
                f"DELETE FROM toc WHERE issue = {n};"]
        if n in cur:
            for rid, toks in cur[n]["fts"].items():
                out.append(f"INSERT INTO pages_fts(pages_fts, rowid, tokens) VALUES ('delete', {rid}, {q(toks)});")
            rows += len(cur[n]["fts"]) * 3          # rows deleted from the three tables, roughly
        if n in new:
            out += new[n]["statements"]
            rows += len(new[n]["statements"]) + len(new[n]["fts"])
    with open(os.path.join(API, "update.sql"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    if rows >= full_rows:
        print(f"full {full_rows}")
    else:
        print(f"update {rows} {len(changed) + len(removed)}")


def commit(mode):
    new = load_dir(NEW)
    if mode == "full":
        shutil.rmtree(CUR, ignore_errors=True)
    os.makedirs(CUR, exist_ok=True)
    for f in os.listdir(CUR):
        if int(f[:-5]) not in new:
            os.remove(os.path.join(CUR, f))
    for n, v in new.items():
        with open(os.path.join(CUR, "%03d.json" % n), "w", encoding="utf-8") as fh:
            json.dump({"hash": v["hash"], "fts": v["fts"]}, fh, ensure_ascii=False)
    shutil.copy(os.path.join(API, "schema.sha256"), SCHEMA_DONE)
    for f in ("imported.sha256", "update.sql"):
        if os.path.exists(os.path.join(API, f)):
            os.remove(os.path.join(API, f))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "legacy":
        legacy()
    elif cmd == "plan":
        plan()
    elif cmd == "commit" and len(sys.argv) > 2:
        commit(sys.argv[2])
    else:
        sys.exit(__doc__)
