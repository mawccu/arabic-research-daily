#!/usr/bin/env python3
"""
Daily research harvester.

Works the way a person would if they had all day: hit every index that will
answer, ask each one several different questions, follow citation threads
forward from whatever looks big, then cross-check what came back before
ranking it.

    python fetch.py                    # last N days, every enabled source
    python fetch.py --days 7           # wider net
    python fetch.py --topic medicine   # one topic
    python fetch.py --quick            # skip the slow passes
    python fetch.py --sources openalex,europepmc

Stdlib only. No API keys. Set contact_email in config.json to enter the
polite pools (higher rate limits at OpenAlex / Crossref, and Unpaywall).
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys

import sources as SRC

ROOT = os.path.dirname(os.path.abspath(__file__))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- strategies

def build_strategies(cfg, topic_name, topic, since, until, quick):
    """One entry per distinct question we know how to ask an index.

    Each is (label, callable) -- the harvester runs them in order and merges.
    Several sources are asked more than once on purpose: a keyword sweep and a
    top-journal sweep surface different papers from the same database.
    """
    kws = [k.lower() for k in topic.get("keywords", [])]
    tiers = cfg["journal_tiers"]
    top_journals = tiers.get("5", []) + tiers.get("4", [])
    jobs = []

    # -- Europe PMC: medicine and life sciences ------------------------------
    if topic.get("europepmc_query"):
        base = f'{topic["europepmc_query"]} AND (FIRST_PDATE:[{since} TO {until}])'

        if kws:
            clause = " OR ".join(f'TITLE:"{k}" OR ABSTRACT:"{k}"' for k in kws)
            jobs.append(("europepmc · keywords",
                         lambda q=f"{base} AND ({clause})":
                         SRC.europepmc(q, label="keywords")))

        if top_journals:
            clause = " OR ".join(f'JOURNAL:"{j}"' for j in top_journals)
            jobs.append(("europepmc · top journals",
                         lambda q=f"{base} AND ({clause})":
                         SRC.europepmc(q, max_pages=3, label="top-journals")))

        # Strong designs regardless of topic vocabulary -- catches the paper
        # about something we never thought to list as a keyword.
        designs = ('PUB_TYPE:"Randomized Controlled Trial" OR '
                   'PUB_TYPE:"Meta-Analysis" OR PUB_TYPE:"Systematic Review"')
        jobs.append(("europepmc · strong designs",
                     lambda q=f"{base} AND ({designs})":
                     SRC.europepmc(q, max_pages=4, label="strong-designs")))

    # -- OpenAlex: everything, every discipline -------------------------------
    if topic.get("openalex_filter"):
        base = (f'{topic["openalex_filter"]},'
                f'from_publication_date:{since},to_publication_date:{until}')

        jobs.append(("openalex · newest",
                     lambda f=base: SRC.openalex(f, max_pages=4, label="newest")))

        # Sorting by citations inside a 3-day window finds what the field is
        # already reacting to -- the closest honest analogue to Scholar's
        # "sort by relevance".
        jobs.append(("openalex · already cited",
                     lambda f=base: SRC.openalex(
                         f, sort="cited_by_count:desc", max_pages=2,
                         label="already-cited")))

        if kws and not quick:
            for kw in kws[:6]:
                jobs.append((f"openalex · search '{kw}'",
                             lambda f=base, k=kw: SRC.openalex(
                                 f, search=k, max_pages=1, label=f"search:{k}")))

    # -- Crossref: the DOI registry itself -----------------------------------
    if topic.get("crossref", True) and not quick:
        filters = (f"from-pub-date:{since},until-pub-date:{until},"
                   f"type:journal-article,has-abstract:true")
        for kw in kws[:4]:
            jobs.append((f"crossref · '{kw}'",
                         lambda f=filters, k=kw: SRC.crossref(
                             f, max_pages=2, query=k, label=f"query:{k}")))

    # -- Preprint servers ----------------------------------------------------
    for server in topic.get("preprint_servers", []):
        jobs.append((f"{server} · preprints",
                     lambda s=server: SRC.biorxiv(s, since, until, label="preprints")))

    # -- arXiv ---------------------------------------------------------------
    if topic.get("arxiv_categories"):
        jobs.append(("arxiv · categories",
                     lambda c=topic["arxiv_categories"]:
                     SRC.arxiv(c, since, until, label="categories")))

    # -- Semantic Scholar: its own relevance model ---------------------------
    if topic.get("semantic_scholar", True) and not quick:
        for kw in kws[:3]:
            jobs.append((f"semanticscholar · '{kw}'",
                         lambda k=kw: SRC.semantic_scholar(
                             k, since, until, label=f"query:{k}")))

    return jobs


# ---------------------------------------------------------------- merge

def fingerprint(p):
    """DOI when we have one, otherwise a squashed title."""
    if p["doi"]:
        return "doi:" + p["doi"]
    return "ti:" + re.sub(r"\W+", "", p["title"].lower())[:90]


def merge(records):
    """Fold duplicates together, keeping the richest value for each field and
    remembering which sources independently surfaced the paper."""
    merged = {}
    for p in records:
        if not p["title"] or len(p["abstract"]) < 200:
            continue
        key = fingerprint(p)
        if key not in merged:
            p["found_by"] = [p["source"]]
            p["strategies"] = [p["strategy"]]
            merged[key] = p
            continue

        cur = merged[key]
        if p["source"] not in cur["found_by"]:
            cur["found_by"].append(p["source"])
        if p["strategy"] and p["strategy"] not in cur["strategies"]:
            cur["strategies"].append(p["strategy"])

        for field in ("doi", "pmid", "pmcid", "epmc_id", "epmc_src", "journal",
                      "authors", "date", "url", "oa_url", "published_as"):
            if not cur.get(field) and p.get(field):
                cur[field] = p[field]
        if len(p["abstract"]) > len(cur["abstract"]):
            cur["abstract"] = p["abstract"]
        cur["citations"] = max(cur["citations"], p["citations"])
        cur["open_access"] = cur["open_access"] or p["open_access"]
        cur["retracted"] = cur["retracted"] or p["retracted"]
        cur["is_preprint"] = cur["is_preprint"] and p["is_preprint"]
        cur["pub_types"] = sorted(set(cur["pub_types"]) | set(p["pub_types"]))

    return list(merged.values())


# ---------------------------------------------------------------- scoring

def normalize_journal(journal):
    j = (journal or "").lower().strip()
    j = re.split(r"\s*[(:]\s*", j)[0]
    j = re.sub(r"^the\s+", "", j)
    return re.sub(r"[^\w\s]", "", j).strip()


def journal_tier(journal, tiers):
    """Exact match first. Substring matching only for multi-word titles --
    bare 'science' / 'nature' / 'cell' appear inside hundreds of unrelated
    journal names."""
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
    candidates = []
    patterns = [
        r"\bn\s*=\s*([\d,]{2,12})",
        r"([\d,]{3,12})\s+(?:participants|patients|adults|children|individuals|subjects|women|men)",
        r"(?:enrolled|included|randomi[sz]ed|followed)\s+([\d,]{3,12})",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
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

    if p.get("retracted"):
        return None, ["excluded: retracted"]
    for bad in cfg["hard_excludes"]:
        if bad in haystack[:400] or bad in p["title"].lower():
            return None, [f"excluded: {bad}"]

    score = 0.0

    tier = journal_tier(p["journal"], cfg["journal_tiers"])
    if tier:
        score += tier
        reasons.append(f"journal tier +{tier}")

    hits = [phrase for phrase in cfg["design_scores"] if phrase in haystack]
    if hits:
        score += sum(cfg["design_scores"][h] for h in hits)
        reasons.append("design: " + ", ".join(hits[:4]))

    n = extract_sample_size(p["abstract"])
    p["sample_size"] = n
    if n:
        bonus = min(5.0, math.log10(n))
        score += bonus
        reasons.append(f"n≈{n:,} +{bonus:.1f}")

    topic_hits = [k for k in cfg["_keywords"] if k in haystack]
    if topic_hits:
        score += min(3.0, len(topic_hits) * 1.0)
        reasons.append("topics: " + ", ".join(topic_hits[:4]))
    else:
        score -= 1.0

    # Independent corroboration: several indexes surfacing the same paper is
    # weak evidence it matters, and strong evidence the record is sound.
    extra = len(p.get("found_by", [])) - 1
    if extra > 0:
        bonus = min(3.0, extra * 1.2)
        score += bonus
        reasons.append(f"found by {len(p['found_by'])} sources +{bonus:.1f}")

    if p["is_preprint"]:
        if p.get("published_as"):
            reasons.append("preprint, since published")
        else:
            score -= 2.0
            reasons.append("preprint -2")

    if p["citations"] > 0:
        score += min(2.0, math.log10(p["citations"] + 1) * 2)
    if p.get("influential"):
        score += min(2.0, p["influential"] * 0.5)
        reasons.append(f"{p['influential']} influential citations")

    if p.get("has_correction"):
        score -= 1.5
        reasons.append("correction issued -1.5")

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
        f"# Shortlist — {day}", "",
        f"Scanned **{stats['total']}** unique papers via **{stats['passes']}** searches "
        f"across {stats['sources']}. Kept **{stats['kept']}** after exclusions.",
        "",
        "> Pick ONE. Then read the actual paper — not this abstract — before scripting.",
        "> Every claim in the episode needs a line you can point to in the PDF.",
        "", "---", "",
    ]
    for i, p in enumerate(ranked, 1):
        flags = []
        if p["is_preprint"] and not p.get("published_as"):
            flags.append("⚠️ PREPRINT — not peer reviewed")
        if p.get("published_as"):
            flags.append(f"ℹ️ preprint, later published: {p['published_as']}")
        if p.get("has_correction"):
            flags.append("⚠️ a correction has been issued for this paper")
        if p["sample_size"] and p["sample_size"] < 100:
            flags.append(f"⚠️ small sample (n≈{p['sample_size']})")

        lines += [
            f"## {i}. {p['title']}", "",
            f"**Score** {p['score']}  ·  **{p['journal'] or 'unknown venue'}**  ·  {p['date']}",
            f"**Found by** {', '.join(p['found_by'])}",
            f"**Link** {p['url']}",
        ]
        if p.get("tldr"):
            lines += ["", f"_One-line summary (Semantic Scholar):_ {p['tldr']}"]
        if flags:
            lines += [""] + flags
        lines += [
            "", f"_Why it ranked here:_ {'; '.join(p['reasons'])}", "",
            "<details><summary>Abstract</summary>", "",
            p["abstract"][:2500], "", "</details>", "",
            "**Editor's call:** ☐ shoot  ☐ hold  ☐ kill — because: ", "", "---", "",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--quick", action="store_true",
                    help="skip the slower per-keyword passes")
    ap.add_argument("--sources", default="",
                    help="comma-separated allowlist, e.g. openalex,europepmc")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    SRC.CONTACT = (cfg.get("contact_email") or "").strip()
    if not SRC.CONTACT:
        log("  (no contact_email in config.json — running in the anonymous, "
            "slower API pools)")

    days = args.days or cfg["lookback_days"]
    top_n = args.top or cfg["shortlist_size"]
    today = dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    until = today.isoformat()
    allow = {s.strip() for s in args.sources.split(",") if s.strip()}

    log(f"\nHarvesting {since} → {until}\n")

    raw, keywords, used_sources, passes = [], [], set(), 0

    for name, topic in cfg["topics"].items():
        if not topic.get("enabled") or (args.topic and args.topic != name):
            continue
        keywords += [k.lower() for k in topic.get("keywords", [])]

        log(f"── {name} " + "─" * (46 - len(name)))
        for label, run in build_strategies(cfg, name, topic, since, until, args.quick):
            if allow and not any(a in label for a in allow):
                continue
            got = run()
            passes += 1
            if got:
                used_sources.add(got[0]["source"])
            raw += got
            log(f"   {label:<34} {len(got):>5}")

        # Follow the thread: what already cites the biggest thing we found?
        if not args.quick and topic.get("openalex_filter"):
            seeds = sorted((p for p in raw if p.get("openalex_id")),
                           key=lambda p: p["citations"], reverse=True)[:3]
            for seed in seeds:
                got = SRC.openalex_citing(seed["openalex_id"], since)
                passes += 1
                raw += got
                if got:
                    log(f"   citation-chase → {seed['title'][:26]:<26} {len(got):>5}")
        log("")

    if not raw:
        log("Nothing fetched. Widen --days, or check connectivity.")
        return 1

    cfg["_keywords"] = sorted(set(keywords))

    papers = merge(raw)
    log(f"{len(raw)} records → {len(papers)} unique papers")

    # Score first so enrichment (which costs API calls) is spent on contenders.
    scored = []
    for p in papers:
        score, reasons = score_paper(p, cfg)
        if score is None:
            continue
        p["score"], p["reasons"] = score, reasons
        scored.append(p)
    scored.sort(key=lambda p: p["score"], reverse=True)

    shortlist = scored[:top_n]
    pool = scored[:max(top_n * 3, 30)]

    log(f"{len(scored)} scored · verifying the top {len(pool)}…")

    # Verification pass: the checks a careful editor makes before committing.
    s2 = SRC.s2_lookup([p["doi"] for p in pool])
    rechecked = 0
    for p in pool:
        extra = s2.get(p["doi"], {})
        if extra:
            p["citations"] = max(p["citations"], extra["citations"])
            p["influential"] = extra["influential"]
            if extra.get("tldr"):
                p["tldr"] = extra["tldr"]

    for p in shortlist:
        if not p["doi"]:
            continue
        rechecked += 1

        # Two independent retraction signals: Crossref's notice list is
        # patchy, OpenAlex's flag is reliable. Either one is disqualifying.
        status = SRC.crossref_status(p["doi"])
        if status:
            p["retracted"] = p["retracted"] or status.get("retracted", False)
            p["has_correction"] = status.get("has_correction", False)
        if SRC.retraction_check(p["doi"]):
            p["retracted"] = True

        if not p["open_access"]:
            p.update({k: v for k, v in SRC.unpaywall(p["doi"]).items() if v})

    # Anything that failed verification is dropped and backfilled.
    survivors = [p for p in shortlist if not p["retracted"]]
    dropped = len(shortlist) - len(survivors)
    if dropped:
        log(f"  dropped {dropped} retracted paper(s) after verification")
    for p in scored[top_n:]:
        if len(survivors) >= top_n:
            break
        if not p["retracted"]:
            survivors.append(p)

    for p in survivors:
        score, reasons = score_paper(p, cfg)
        p["score"], p["reasons"] = (score, reasons) if score is not None else (p["score"], p["reasons"])
    survivors.sort(key=lambda p: p["score"], reverse=True)

    seen = set()
    for p in survivors:
        p["uid"] = p["doi"] or re.sub(r"\W+", "", p["title"].lower())[:90]
        seen.add(p["uid"])

    outdir = os.path.join(ROOT, "out")
    os.makedirs(outdir, exist_ok=True)
    md = os.path.join(outdir, f"{until}.md")
    js = os.path.join(outdir, f"{until}.json")

    stats = {
        "total": len(papers), "kept": len(scored), "passes": passes,
        "verified": rechecked,
        "sources": ", ".join(sorted(used_sources)),
    }
    write_markdown(md, until, survivors, stats)
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"date": until, "stats": stats, "shortlist": survivors}, f,
                  ensure_ascii=False, indent=2)

    log(f"\n{passes} searches · {len(papers)} unique → top {len(survivors)}")
    log(f"  {md}\n  {js}\n")
    for i, p in enumerate(survivors[:5], 1):
        log(f"  {i}. [{p['score']:>5}] {p['title'][:88]}")
        log(f"     {', '.join(p['found_by'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
