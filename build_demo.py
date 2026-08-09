#!/usr/bin/env python3
"""
Build the static demo in docs/ for GitHub Pages.

Copies web/ verbatim, bakes the latest shortlist into demo-data.js, and injects
that script before app.js. app.js sees window.DEMO_DATA and answers every API
call locally instead of hitting the Python server.

    python build_demo.py
    git add docs && git commit -m "rebuild demo" && git push

Nothing here changes the local app -- web/ stays the single source of truth.
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(ROOT, "out")
DOCS = os.path.join(ROOT, "docs")
UA = "arabic-research-daily/0.1 (demo build)"

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def latest_run():
    runs = sorted(f for f in os.listdir(OUT) if f.endswith(".json"))
    if not runs:
        sys.exit("No runs in out/. Run: python fetch.py")
    path = os.path.join(OUT, runs[-1])
    with open(path, encoding="utf-8") as f:
        return json.load(f), runs[-1]


def fetch_references(shortlist, limit=6):
    """Bake reference lists for the top few papers so the Sources tab isn't dead."""
    refs = {}
    for p in shortlist[:limit]:
        src, pid = p.get("epmc_src"), p.get("epmc_id")
        if not src or not pid:
            continue
        url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/{src}/{pid}/"
               f"references?format=json&pageSize=60")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  ! refs {pid}: {e}")
            continue

        items = data.get("referenceList", {}).get("reference", [])
        refs[pid] = [{
            "title": x.get("title", ""),
            "authors": x.get("authorString", ""),
            "journal": x.get("journalAbbreviation", ""),
            "year": x.get("pubYear", ""),
            "doi": x.get("doi", ""),
        } for x in items]
        print(f"  refs {pid}: {len(refs[pid])}")
        time.sleep(0.4)
    return refs


def main():
    run, name = latest_run()
    shortlist = run.get("shortlist", [])
    print(f"building demo from out/{name} ({len(shortlist)} papers)")

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    print("fetching reference lists…")
    references = fetch_references(shortlist)

    if os.path.isdir(DOCS):
        shutil.rmtree(DOCS)
    shutil.copytree(WEB, DOCS)

    payload = {
        "date": run.get("date", ""),
        "stats": run.get("stats", {}),
        "shortlist": shortlist,
        "config": config,
        "references": references,
    }
    with open(os.path.join(DOCS, "demo-data.js"), "w", encoding="utf-8") as f:
        f.write("window.DEMO_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    index_path = os.path.join(DOCS, "index.html")
    with open(index_path, encoding="utf-8") as f:
        html = f.read()

    if "demo-data.js" not in html:
        html = html.replace('<script src="/app.js"></script>',
                            '<script src="demo-data.js"></script>\n'
                            '<script src="app.js"></script>')
    # Pages serves from a subpath, so absolute asset URLs would 404.
    html = html.replace('href="/style.css"', 'href="style.css"')
    html = re.sub(r'<title>.*?</title>',
                  '<title>Research Daily — demo</title>', html, flags=re.S)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Pages runs Jekyll by default, which ignores files it doesn't understand.
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    size = sum(os.path.getsize(os.path.join(DOCS, f)) for f in os.listdir(DOCS))
    print(f"\ndocs/ built — {len(os.listdir(DOCS))} files, {size/1024:.0f} KB")
    print("  git add docs && git commit -m 'rebuild demo' && git push")


if __name__ == "__main__":
    main()
