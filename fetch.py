#!/usr/bin/env python3
"""
Daily research discovery for the Arabic research-daily pipeline.

Pulls newly published papers from Europe PMC (medicine / life sciences, incl.
preprints) and arXiv (AI / CS), scores them with transparent heuristics, and
writes a ranked shortlist for a human editor to pick ONE episode from.

Stdlib only. No API keys. No pip install.

    python fetch.py                  # last N days, writes out/YYYY-MM-DD.{md,json}
    python fetch.py --days 7         # wider net
    python fetch.py --topic medicine # single topic
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = "arabic-research-daily/0.1 (editorial shortlist tool)"

# Windows consoles default to cp1252 and choke on arrows / Arabic.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


# ---------------------------------------------------------------- http

def get(url, retries=4, timeout=45):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ! failed: {e}", file=sys.stderr)
                return None
            # arXiv returns 429 freely; it wants ~3s between requests.
            time.sleep(4.0 * (attempt + 1))
    return None


# ---------------------------------------------------------------- sources

def europepmc_queries(cfg, topic_cfg, since, until):
    """Two complementary sweeps:
      1. topic keywords anywhere in title/abstract — the bulk of the funnel
      2. anything at all in a top-tier journal — so a major NEJM/Lancet paper
         can't slip past just because it uses vocabulary we didn't list
    """
    base = f'{topic_cfg["europepmc_query"]} AND (FIRST_PDATE:[{since} TO {until}])'

    kws = topic_cfg.get("keywords", [])
    kw_clause = " OR ".join(f'TITLE:"{k}" OR ABSTRACT:"{k}"' for k in kws)

    top_journals = cfg["journal_tiers"].get("5", []) + cfg["journal_tiers"].get("4", [])
    jr_clause = " OR ".join(f'JOURNAL:"{j}"' for j in top_journals)

    queries = []
    if kw_clause:
        queries.append(f"{base} AND ({kw_clause})")
    if jr_clause:
        queries.append(f"{base} AND ({jr_clause})")
    return queries or [base]


def fetch_europepmc(cfg, topic_cfg, since, until):
    """Europe PMC covers PubMed/MEDLINE, PMC full text, and preprints in one API."""
    papers = []
    for query in europepmc_queries(cfg, topic_cfg, since, until):
        papers += _europepmc_search(cfg, query)
    return papers


def _europepmc_search(cfg, query):
    results, cursor, page_size = [], "*", 200
    for page in range(cfg.get("max_pages", 10)):
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + urllib.parse.urlencode({
                "query": query,
                "format": "json",
                "pageSize": page_size,
                "cursorMark": cursor,
                "resultType": "core",
                "sort": "P_PDATE_D desc",
            })
        )
        raw = get(url)
        if not raw:
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            break

        batch = data.get("resultList", {}).get("result", [])
        results += batch
        next_cursor = data.get("nextCursorMark")
        if not batch or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    else:
        print(f"  (stopped at the page cap; window may extend further)", file=sys.stderr)

    papers = []
    for r in results:
        abstract = (r.get("abstractText") or "").strip()
        if len(abstract) < 200:          # no abstract, nothing to evaluate
            continue
        papers.append({
            "source": "europepmc",
            "epmc_id": r.get("id", ""),
            "epmc_src": r.get("source", "MED"),
            "pmcid": r.get("pmcid", ""),
            "open_access": r.get("isOpenAccess") == "Y",
            "authors": (r.get("authorString") or "").strip(),
            "title": (r.get("title") or "").strip().rstrip("."),
            "abstract": re.sub(r"<[^>]+>", "", abstract),
            "journal": (r.get("journalInfo", {}).get("journal", {}).get("title")
                        or r.get("bookOrReportDetails", {}).get("publisher")
                        or ("preprint" if r.get("source") == "PPR" else "")).strip(),
            "date": r.get("firstPublicationDate", ""),
            "doi": r.get("doi", ""),
            "pmid": r.get("pmid", ""),
            "is_preprint": r.get("source") == "PPR",
            "citations": int(r.get("citedByCount") or 0),
            "pub_types": [t.lower() for t in (r.get("pubTypeList", {}) or {}).get("pubType", [])],
            "url": (f"https://doi.org/{r['doi']}" if r.get("doi")
                    else f"https://europepmc.org/article/{r.get('source','MED')}/{r.get('id','')}"),
        })
    return papers


def fetch_arxiv(cfg, topic_cfg, since, until):
    """arXiv's submittedDate range queries are slow and time out; instead we page
    through newest-first and stop once we walk past the lookback window."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    cats = " OR ".join(f"cat:{c}" for c in topic_cfg["arxiv_categories"])
    page_size, max_pages = 200, 6
    papers, done = [], False

    for page in range(max_pages):
        if page:
            time.sleep(3)  # arXiv asks for one call per 3 seconds
        url = (
            "https://export.arxiv.org/api/query?"
            + urllib.parse.urlencode({
                "search_query": f"({cats})",
                "start": page * page_size,
                "max_results": page_size,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            })
        )
        raw = get(url)
        if not raw:
            break
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            break

        entries = root.findall("a:entry", ns)
        if not entries:
            break

        for e in entries:
            def text(tag):
                node = e.find(f"a:{tag}", ns)
                return (node.text or "").strip() if node is not None else ""

            published = text("published")[:10]
            if published and published < since:
                done = True
                continue
            if published > until:
                continue

            abstract = re.sub(r"\s+", " ", text("summary"))
            if len(abstract) < 200:
                continue
            authors = ", ".join(
                (a.findtext("a:name", "", ns) or "").strip()
                for a in e.findall("a:author", ns)
            )
            papers.append({
                "source": "arxiv",
                "epmc_id": "",
                "epmc_src": "",
                "pmcid": "",
                "open_access": True,
                "authors": authors,
                "title": re.sub(r"\s+", " ", text("title")),
                "abstract": abstract,
                "journal": "arXiv preprint",
                "date": published,
                "doi": "",
                "pmid": "",
                "is_preprint": True,
                "citations": 0,
                "pub_types": ["preprint"],
                "url": text("id"),
            })

        if done or len(entries) < page_size:
            break
    else:
        print(f"  (hit the {max_pages * page_size}-result page cap; older items in "
              f"the window were not scanned)", file=sys.stderr)

    return papers[:cfg["max_per_source"] * 3]


# ---------------------------------------------------------------- scoring

def normalize_journal(journal):
    """'Lancet (London, England)' -> 'lancet';
       'Daru : journal of ... Medical Sciences' -> 'daru'."""
    j = (journal or "").lower().strip()
    j = re.split(r"\s*[(:]\s*", j)[0]
    j = re.sub(r"^the\s+", "", j)
    return re.sub(r"[^\w\s]", "", j).strip()


def journal_tier(journal, tiers):
    """Exact match first. Substring matching is allowed only for multi-word
    titles, because bare 'science'/'nature'/'cell' appear inside hundreds of
    unrelated journal names."""
    j = normalize_journal(journal)
    if not j:
        return 0

    ordered = sorted(tiers, key=int, reverse=True)
    for score in ordered:
        if any(j == name for name in tiers[score]):
            return int(score)
    for score in ordered:
        for name in tiers[score]:
            if " " in name and name in j:
                return int(score)
    return 0


def extract_sample_size(text):
    """Best-effort largest participant count mentioned in the abstract."""
    candidates = []
    patterns = [
        r"\bn\s*=\s*([\d,]{2,12})",
        r"([\d,]{3,12})\s+(?:participants|patients|adults|children|individuals|subjects|women|men)",
        r"(?:enrolled|included|randomi[sz]ed|followed)\s+([\d,]{3,12})",
    ]
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            try:
                v = int(m.group(1).replace(",", ""))
                if 5 <= v <= 50_000_000:
                    candidates.append(v)
            except ValueError:
                pass
    return max(candidates) if candidates else 0


def score_paper(p, cfg):
    haystack = f"{p['title']} {p['abstract']} {' '.join(p['pub_types'])}".lower()
    reasons = []

    for bad in cfg["hard_excludes"]:
        if bad in haystack[:400] or bad in p["title"].lower():
            return None, [f"excluded: {bad}"]

    score = 0.0

    tier = journal_tier(p["journal"], cfg["journal_tiers"])
    if tier:
        score += tier
        reasons.append(f"journal tier +{tier}")

    design_hits = []
    for phrase, pts in cfg["design_scores"].items():
        if phrase in haystack:
            score += pts
            design_hits.append(phrase)
    if design_hits:
        reasons.append("design: " + ", ".join(design_hits[:4]))

    n = extract_sample_size(p["abstract"])
    if n:
        bonus = min(5.0, math.log10(n))
        score += bonus
        reasons.append(f"n≈{n:,} +{bonus:.1f}")
    p["sample_size"] = n

    topic_hits = [k for k in cfg["_keywords"] if k in haystack]
    if topic_hits:
        bonus = min(3.0, len(topic_hits) * 1.0)
        score += bonus
        reasons.append("topics: " + ", ".join(topic_hits[:4]))
    else:
        score -= 1.0

    if p["is_preprint"]:
        score -= 2.0
        reasons.append("preprint -2")

    if p["citations"] > 0:
        score += min(2.0, math.log10(p["citations"] + 1) * 2)

    # Reader-facing hooks: things a general Arabic audience can act on.
    for hook, pts in (("first", 1.0), ("largest", 1.5), ("reversed", 1.0),
                      ("no benefit", 1.5), ("no association", 1.0),
                      ("increased risk", 1.0), ("reduced risk", 1.0)):
        if hook in p["title"].lower():
            score += pts
            reasons.append(f"hook '{hook}' +{pts}")

    return round(score, 1), reasons


# ---------------------------------------------------------------- output

def write_markdown(path, day, ranked, stats):
    lines = [
        f"# Shortlist — {day}",
        "",
        f"Scanned **{stats['total']}** papers from {stats['sources']}. "
        f"Kept **{stats['kept']}** after exclusions. Top {len(ranked)} below.",
        "",
        "> Pick ONE. Then read the actual paper — not this abstract — before scripting.",
        "> Every claim in the episode needs a line you can point to in the PDF.",
        "",
        "---",
        "",
    ]
    for i, p in enumerate(ranked, 1):
        flags = []
        if p["is_preprint"]:
            flags.append("⚠️ PREPRINT — not peer reviewed")
        if p["sample_size"] and p["sample_size"] < 100:
            flags.append(f"⚠️ small sample (n≈{p['sample_size']})")

        lines += [
            f"## {i}. {p['title']}",
            "",
            f"**Score** {p['score']}  ·  **{p['journal'] or 'unknown venue'}**  ·  {p['date']}",
            f"**Link** {p['url']}",
        ]
        if flags:
            lines.append("")
            lines += [f"{f}" for f in flags]
        lines += [
            "",
            f"_Why it ranked here:_ {'; '.join(p['reasons'])}",
            "",
            "<details><summary>Abstract</summary>",
            "",
            p["abstract"][:2500],
            "",
            "</details>",
            "",
            "**Editor's call:** ☐ shoot  ☐ hold  ☐ kill — because: ",
            "",
            "---",
            "",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--topic", default=None, help="only run one topic key from config")
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    days = args.days or cfg["lookback_days"]
    top_n = args.top or cfg["shortlist_size"]
    today = dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    until = today.isoformat()

    papers, used_sources = [], []
    all_keywords = []

    for name, tcfg in cfg["topics"].items():
        if not tcfg.get("enabled") or (args.topic and args.topic != name):
            continue
        all_keywords += [k.lower() for k in tcfg.get("keywords", [])]

        if "europepmc_query" in tcfg:
            print(f"→ Europe PMC / {name} ({since} → {until})")
            got = fetch_europepmc(cfg, tcfg, since, until)
            print(f"  {len(got)} with usable abstracts")
            papers += got
            used_sources.append(f"Europe PMC/{name}")

        if "arxiv_categories" in tcfg:
            print(f"→ arXiv / {name} ({since} → {until})")
            got = fetch_arxiv(cfg, tcfg, since, until)
            print(f"  {len(got)} with usable abstracts")
            papers += got
            used_sources.append(f"arXiv/{name}")

    if not papers:
        print("\nNothing fetched. Widen --days or check connectivity.", file=sys.stderr)
        return 1

    cfg["_keywords"] = sorted(set(all_keywords))

    seen, deduped = set(), []
    for p in papers:
        key = p["doi"].lower() or re.sub(r"\W+", "", p["title"].lower())[:90]
        if key in seen:
            continue
        seen.add(key)
        p["uid"] = key           # stable across runs: keys the UI's saved work
        deduped.append(p)

    kept = []
    for p in deduped:
        score, reasons = score_paper(p, cfg)
        if score is None:
            continue
        p["score"], p["reasons"] = score, reasons
        kept.append(p)

    ranked = sorted(kept, key=lambda p: p["score"], reverse=True)[:top_n]

    outdir = os.path.join(ROOT, "out")
    os.makedirs(outdir, exist_ok=True)
    md = os.path.join(outdir, f"{until}.md")
    js = os.path.join(outdir, f"{until}.json")

    stats = {"total": len(deduped), "kept": len(kept), "sources": ", ".join(used_sources)}
    write_markdown(md, until, ranked, stats)
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"date": until, "stats": stats, "shortlist": ranked}, f,
                  ensure_ascii=False, indent=2)

    print(f"\n{len(deduped)} unique → {len(kept)} scored → top {len(ranked)}")
    print(f"  {md}\n  {js}\n")
    for i, p in enumerate(ranked[:5], 1):
        print(f"  {i}. [{p['score']:>5}] {p['title'][:95]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
