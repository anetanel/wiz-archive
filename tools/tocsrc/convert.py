import json, os, re, sys, glob
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "toc")
def parse(path):
    issues, cur = {}, None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or (line.startswith("# ")): continue
        if re.match(r"^#\d{3}$", line):
            cur = issues.setdefault(line[1:], {"entries": []}); continue
        if line.startswith("@date "):
            v = line[6:].strip(); est = v.endswith("~"); y, m = v.rstrip("~").split("-")
            cur.update(year=int(y), month=int(m), date_estimated=est); continue
        if line.startswith("@note "): cur["notes"] = line[6:].strip(); continue
        if line.startswith("@swap "): cur.setdefault("swap", []).extend(line[6:].split()); continue
        if line.strip() == "@keep": cur["keep"] = True; continue
        page, sec, title = line.split("|", 2)
        e = {}
        if "-" in page:
            a, b = page.split("-"); e["page"], e["page_end"] = int(a), int(b)
        else: e["page"] = int(page)
        e["title"] = title.strip()
        if sec.strip(): e["section"] = sec.strip()
        cur["entries"].append({"page": e["page"], **({"page_end": e["page_end"]} if "page_end" in e else {}), "title": e["title"], **({"section": e["section"]} if "section" in e else {})})
    return issues
done = []
for f in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "b*.txt"))):
    for iss, d in parse(f).items():
        path = os.path.join(OUT, iss + ".json")
        if d.pop("keep", False):
            old = json.load(open(path, encoding="utf-8"))
            for k in ("year", "month", "date_estimated", "notes"):
                if k in d: old[k] = d[k]
            d = old
        else:
            pages = [e["page"] for e in d["entries"]]
            if pages != sorted(pages): print("note: non-monotonic order", iss)
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done.append(iss)
print(len(done), "issues:", " ".join(sorted(done)))
