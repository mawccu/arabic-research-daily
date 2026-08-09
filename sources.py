#!/usr/bin/env python3
"""
Source adapters for the research harvester.

Every function here returns a list of papers in one normalised shape, so the
harvester can merge results from very different APIs without special-casing.

All sources are free and keyless. Set `contact_email` in config.json to enter
the "polite pools" of OpenAlex, Crossref and Unpaywall -- they give identified
callers higher rate limits and better service.

Deliberately absent: Google Scholar. It has no API, its terms forbid automated
access, and it enforces that with CAPTCHAs and IP blocks. OpenAlex + Crossref +
Semantic Scholar together index the same literature and can be queried properly.
"""

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "arabic-research-daily/0.2 (editorial shortlist tool)"
CONTACT = ""          # set by fetch.py from config.json

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


# ---------------------------------------------------------------- http

_last_call = {}
_rate_lock = threading.Lock()

# Minimum seconds between calls per host, from each API's published guidance.
RATE = {
    "export.arxiv.org": 3.0,
    "api.semanticscholar.org": 3.5,
    "api.crossref.org": 0.6,
    "api.openalex.org": 0.15,
    "api.unpaywall.org": 0.15,
    "www.ebi.ac.uk": 0.2,
    "api.biorxiv.org": 0.4,
    "eutils.ncbi.nlm.nih.gov": 0.4,
}


def _throttle(url):
    host = urllib.parse.urlparse(url).netloc
    gap = RATE.get(host, 0.3)
    with _rate_lock:
        wait = gap - (time.monotonic() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def get(url, retries=3, timeout=45, accept=None):
    """GET with per-host throttling and backoff. Returns text, or None."""
    headers = {"User-Agent": f"{UA} {CONTACT}".strip()}
    if accept:
        headers["Accept"] = accept

    for attempt in range(retries):
        _throttle(url)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):          # asked for something that isn't there
                return None
            if e.code == 429:
                # Shared anonymous pools (Semantic Scholar especially) throttle
                # hard. Back off properly rather than hammering.
                if attempt == retries - 1:
                    print(f"  ! rate-limited by "
                          f"{urllib.parse.urlparse(url).netloc}", file=sys.stderr)
                    return None
                time.sleep(8.0 * (attempt + 1))
                continue
            if attempt == retries - 1:
                return None
            time.sleep(2.5 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(2.5 * (attempt + 1))
    return None


def get_json(url, **kw):
    raw = get(url, accept="application/json", **kw)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _q(params):
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# ---------------------------------------------------------------- shaping

def blank(**kw):
    """The normalised paper record every source must produce."""
    p = {
        "source": "", "strategy": "", "title": "", "abstract": "", "journal": "",
        "date": "", "doi": "", "pmid": "", "pmcid": "", "epmc_id": "", "epmc_src": "",
        "authors": "", "citations": 0, "is_preprint": False, "open_access": False,
        "oa_url": "", "retracted": False, "pub_types": [], "url": "",
        "openalex_id": "", "published_as": "",
    }
    p.update(kw)
    return p


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


# Acronyms worth preserving when de-shouting a title. An allowlist rather than
# a "short word" rule: length can't distinguish RCT from RISK, and the failure
# mode here is right. Missing an acronym renders it lowercase -- mildly wrong
# but calm; wrongly keeping a word leaves the shouting we set out to remove.
_ACRONYMS = {
    "RCT", "RCTS", "HIV", "AIDS", "COVID", "SARS", "MERS", "TB", "STI", "HPV",
    "BMI", "BP", "LDL", "HDL", "CRP", "HBA1C", "EGFR", "CKD", "COPD", "CVD",
    "MACE", "MI", "HF", "AF", "CAD", "PAD", "DVT", "PE", "IBD", "IBS", "GERD",
    "NAFLD", "MASLD", "T1D", "T2D", "PCOS", "ADHD", "ASD", "PTSD", "MDD", "OCD",
    "MRI", "CT", "PET", "ECG", "EEG", "DEXA", "ICU", "ER", "OR", "QOL", "PROM",
    "WHO", "NHS", "CDC", "FDA", "NICE", "NIH", "USA", "UK", "EU", "US",
    "DNA", "RNA", "MRNA", "PCR", "GWAS", "SNP", "CRISPR", "IGF", "TNF", "IL",
    "AI", "ML", "LLM", "LLMS", "NLP", "GPU", "API", "GPT", "BERT", "CNN", "RNN",
}


def clean_title(text):
    """Some publishers register titles in block capitals. Left as-is they shout
    over everything else in the shortlist for no editorial reason.

    Sentence case is the safe target: it reads naturally and, unlike title
    case, doesn't require guessing which small words to capitalise. Genuine
    acronyms are kept -- anything with a digit (SGLT2, GLP-1, COVID-19) or a
    short all-caps token that isn't an ordinary English word (RCT, HIV, BMI).
    """
    t = clean(text).rstrip(".")
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 12 or sum(c.isupper() for c in letters) / len(letters) <= 0.8:
        return t

    words = []
    for w in t.split():
        core = re.sub(r"[^\w]", "", w)
        # The digit rule is length-capped: it should catch SGLT2 and COVID-19,
        # not long hyphenated words that happen to end in a numeral.
        acronym = (core.upper() in _ACRONYMS
                   or (len(core) <= 8 and any(ch.isdigit() for ch in core)))
        words.append(w if acronym else w.lower())

    out = " ".join(words)
    # Capitalise the opening letter, and anything starting a new clause.
    out = re.sub(r"(^|[.:;?!]\s+)([a-z])",
                 lambda m: m.group(1) + m.group(2).upper(), out)
    return out


def norm_doi(doi):
    doi = (doi or "").strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".")


# ---------------------------------------------------------------- Europe PMC

def europepmc(query, page_size=200, max_pages=10, label="europepmc"):
    """MEDLINE + PMC + preprints. The workhorse for medicine."""
    out, cursor = [], "*"
    for _ in range(max_pages):
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + _q({
            "query": query, "format": "json", "pageSize": page_size,
            "cursorMark": cursor, "resultType": "core", "sort": "P_PDATE_D desc",
        })
        data = get_json(url)
        if not data:
            break
        batch = data.get("resultList", {}).get("result", [])
        for r in batch:
            abstract = clean(r.get("abstractText"))
            if len(abstract) < 200:
                continue
            out.append(blank(
                source="europepmc", strategy=label,
                epmc_id=r.get("id", ""), epmc_src=r.get("source", "MED"),
                pmcid=r.get("pmcid", ""), pmid=r.get("pmid", ""),
                doi=norm_doi(r.get("doi")),
                title=clean_title(r.get("title")),
                abstract=abstract,
                journal=clean(r.get("journalInfo", {}).get("journal", {}).get("title")
                              or ("preprint" if r.get("source") == "PPR" else "")),
                date=r.get("firstPublicationDate", ""),
                authors=clean(r.get("authorString")),
                citations=int(r.get("citedByCount") or 0),
                is_preprint=r.get("source") == "PPR",
                open_access=r.get("isOpenAccess") == "Y",
                pub_types=[t.lower() for t in (r.get("pubTypeList") or {}).get("pubType", [])],
                url=(f"https://doi.org/{r['doi']}" if r.get("doi")
                     else f"https://europepmc.org/article/{r.get('source','MED')}/{r.get('id','')}"),
            ))
        nxt = data.get("nextCursorMark")
        if not batch or not nxt or nxt == cursor:
            break
        cursor = nxt
    return out


# ---------------------------------------------------------------- OpenAlex

def _openalex_abstract(inverted):
    """OpenAlex ships abstracts as a word -> [positions] index. Rebuild it."""
    if not inverted:
        return ""
    slots = {}
    for word, positions in inverted.items():
        for pos in positions:
            slots[pos] = word
    return " ".join(slots[i] for i in sorted(slots))


OPENALEX_FIELDS = ",".join([
    "id", "doi", "title", "publication_date", "type", "cited_by_count",
    "is_retracted", "open_access", "primary_location", "authorships",
    "abstract_inverted_index", "referenced_works", "language",
])


def openalex(filters, sort="publication_date:desc", per_page=200,
             max_pages=6, label="openalex", search=None):
    """Every discipline, one API. The open successor to Microsoft Academic --
    and the honest replacement for scraping Google Scholar."""
    out, cursor = [], "*"
    for _ in range(max_pages):
        params = {
            "filter": filters, "per-page": per_page, "cursor": cursor,
            "select": OPENALEX_FIELDS, "sort": sort,
        }
        if search:
            params["search"] = search
            params.pop("sort", None)     # relevance ordering when searching
        if CONTACT:
            params["mailto"] = CONTACT

        data = get_json("https://api.openalex.org/works?" + _q(params))
        if not data:
            break

        batch = data.get("results", [])
        for w in batch:
            abstract = clean(_openalex_abstract(w.get("abstract_inverted_index")))
            if len(abstract) < 200:
                continue
            loc = w.get("primary_location") or {}
            src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
            venue = clean(src.get("display_name"))
            names = [clean((a.get("author") or {}).get("display_name"))
                     for a in (w.get("authorships") or [])[:12]]
            oa = w.get("open_access") or {}

            out.append(blank(
                source="openalex", strategy=label,
                openalex_id=(w.get("id") or "").rsplit("/", 1)[-1],
                doi=norm_doi(w.get("doi")),
                title=clean_title(w.get("title")),
                abstract=abstract,
                journal=venue,
                date=w.get("publication_date", ""),
                authors=", ".join(n for n in names if n),
                citations=int(w.get("cited_by_count") or 0),
                is_preprint=(w.get("type") == "preprint"
                             or bool(src.get("is_in_doaj") is None and "rxiv" in venue.lower())),
                open_access=bool(oa.get("is_oa")),
                oa_url=oa.get("oa_url") or "",
                retracted=bool(w.get("is_retracted")),
                url=(f"https://doi.org/{norm_doi(w.get('doi'))}"
                     if w.get("doi") else w.get("id", "")),
            ))

        cursor = (data.get("meta") or {}).get("next_cursor")
        if not batch or not cursor:
            break
    return out


def openalex_citing(openalex_id, since, label="citation-chase"):
    """Papers citing a given work -- how a human follows a thread forward."""
    if not openalex_id:
        return []
    return openalex(f"cites:{openalex_id},from_publication_date:{since}",
                    max_pages=2, label=label)


# ---------------------------------------------------------------- Crossref

def crossref(filters, rows=100, max_pages=5, label="crossref", query=None):
    """Every registered DOI, all publishers. Best for catching papers the
    subject indexes haven't picked up yet."""
    out, cursor = [], "*"
    for _ in range(max_pages):
        params = {"filter": filters, "rows": rows, "cursor": cursor}
        if query:
            params["query.bibliographic"] = query
        if CONTACT:
            params["mailto"] = CONTACT

        data = get_json("https://api.crossref.org/works?" + _q(params))
        if not data:
            break
        msg = data.get("message", {})
        batch = msg.get("items", [])

        for it in batch:
            abstract = clean(it.get("abstract"))
            if len(abstract) < 200:
                continue
            title = clean_title((it.get("title") or [""])[0])
            if not title:
                continue
            names = [clean(f"{a.get('given','')} {a.get('family','')}")
                     for a in (it.get("author") or [])[:12]]
            parts = ((it.get("published") or {}).get("date-parts") or [[]])[0]
            date = "-".join(f"{p:02d}" if i else str(p)
                            for i, p in enumerate(parts)) if parts else ""

            # `update-to` here means this record IS a notice about another
            # paper -- a retraction or correction announcement, not a study.
            if any((u.get("type") or "").lower() in
                   ("retraction", "correction", "erratum", "addendum",
                    "withdrawal", "removal")
                   for u in (it.get("update-to") or [])):
                continue

            out.append(blank(
                source="crossref", strategy=label,
                doi=norm_doi(it.get("DOI")),
                title=title, abstract=abstract,
                journal=clean((it.get("container-title") or [""])[0]),
                date=date,
                authors=", ".join(n for n in names if n),
                citations=int(it.get("is-referenced-by-count") or 0),
                pub_types=[(it.get("type") or "").replace("-", " ")],
                url=it.get("URL", ""),
            ))

        cursor = msg.get("next-cursor")
        if not batch or not cursor:
            break
    return out


# ---------------------------------------------------------------- arXiv

def arxiv(categories, since, until, max_pages=6, page_size=200, label="arxiv"):
    """arXiv's date-range queries time out, so page newest-first and stop."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    cats = " OR ".join(f"cat:{c}" for c in categories)
    out, done, newest = [], False, ""

    for page in range(max_pages):
        url = "https://export.arxiv.org/api/query?" + _q({
            "search_query": f"({cats})", "start": page * page_size,
            "max_results": page_size, "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
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
            newest = max(newest, published)
            if published and published < since:
                done = True
                continue
            if published > until:
                continue
            abstract = clean(text("summary"))
            if len(abstract) < 200:
                continue

            doi_node = e.find("{http://arxiv.org/schemas/atom}doi")
            out.append(blank(
                source="arxiv", strategy=label,
                title=clean_title(text("title")), abstract=abstract,
                journal="arXiv preprint", date=published,
                doi=norm_doi(doi_node.text if doi_node is not None else ""),
                authors=", ".join(clean(a.findtext("a:name", "", ns))
                                  for a in e.findall("a:author", ns)),
                is_preprint=True, open_access=True,
                pub_types=["preprint"], url=text("id"),
            ))

        if done or len(entries) < page_size:
            break

    if not out and newest:
        # arXiv doesn't announce at weekends, so a short window ending on a
        # Monday legitimately contains nothing. Say so rather than showing 0.
        print(f"  (arXiv has nothing in {since}→{until}; newest submission "
              f"is {newest} — try --days 7)", file=sys.stderr)
    return out


# ---------------------------------------------------------------- preprints

def biorxiv(server, since, until, max_pages=8, label="biorxiv"):
    """bioRxiv / medRxiv. `published` tells you if a journal later took it."""
    out = []
    cursor = 0
    for _ in range(max_pages):
        url = f"https://api.biorxiv.org/details/{server}/{since}/{until}/{cursor}"
        data = get_json(url)
        if not data:
            break
        batch = data.get("collection", []) or []
        for r in batch:
            abstract = clean(r.get("abstract"))
            if len(abstract) < 200:
                continue
            published = (r.get("published") or "").strip()
            out.append(blank(
                source=server, strategy=label,
                doi=norm_doi(r.get("doi")),
                title=clean_title(r.get("title")), abstract=abstract,
                journal=f"{server} preprint", date=r.get("date", ""),
                authors=clean(r.get("authors")),
                is_preprint=True, open_access=True,
                published_as="" if published.upper() in ("", "NA") else published,
                pub_types=["preprint"],
                url=f"https://doi.org/{norm_doi(r.get('doi'))}",
            ))
        total = int((data.get("messages") or [{}])[0].get("total", 0) or 0)
        cursor += len(batch)
        if not batch or cursor >= total:
            break
    return out


# ---------------------------------------------------------------- Semantic Scholar

def semantic_scholar(query, since, until, limit=100, label="s2"):
    """Rate-limited hard without a key, so it runs as a targeted pass rather
    than a bulk sweep. Worth it for its own relevance ranking.

    The date filter must be a closed range -- an open-ended `since:` is
    rejected."""
    fields = ("title,abstract,venue,publicationDate,externalIds,citationCount,"
              "influentialCitationCount,isOpenAccess,openAccessPdf,authors,"
              "publicationTypes")
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + _q({
        "query": query, "fields": fields, "limit": min(limit, 100),
        "publicationDateOrYear": f"{since}:{until}",
    })
    data = get_json(url)
    if not data:
        return []

    out = []
    for r in data.get("data", []) or []:
        abstract = clean(r.get("abstract"))
        if len(abstract) < 200:
            continue
        ext = r.get("externalIds") or {}
        pdf = r.get("openAccessPdf") or {}
        out.append(blank(
            source="semanticscholar", strategy=label,
            doi=norm_doi(ext.get("DOI")), pmid=str(ext.get("PubMed") or ""),
            title=clean_title(r.get("title")), abstract=abstract,
            journal=clean(r.get("venue")),
            date=r.get("publicationDate") or "",
            authors=", ".join(clean(a.get("name")) for a in (r.get("authors") or [])[:12]),
            citations=int(r.get("citationCount") or 0),
            open_access=bool(r.get("isOpenAccess")),
            oa_url=pdf.get("url") or "",
            pub_types=[str(t).lower() for t in (r.get("publicationTypes") or [])],
            url=f"https://doi.org/{norm_doi(ext.get('DOI'))}" if ext.get("DOI") else "",
        ))
    return out


# ---------------------------------------------------------------- enrichment

def crossref_status(doi):
    """Notices issued *about* this paper, plus its reference count.

    The field is `updated-by` -- notices pointing at this record. (`update-to`
    is the opposite direction: it appears on the retraction notice and names
    the paper being retracted, so reading it here finds nothing.) Crossref
    populates this inconsistently, so treat a negative as "no evidence", not
    "not retracted" -- retraction_check() below is the stronger signal.
    """
    if not doi:
        return {}
    data = get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    if not data:
        return {}
    m = data.get("message", {})
    notices = [(u.get("type") or "").lower() for u in (m.get("updated-by") or [])]
    return {
        "retracted": any("retract" in n for n in notices),
        "has_correction": any(n in ("correction", "erratum", "addendum")
                              for n in notices),
        "references": int(m.get("references-count") or 0),
    }


def retraction_check(doi):
    """OpenAlex tracks retractions properly, including papers Crossref's
    `updated-by` never got populated for."""
    if not doi:
        return None
    data = get_json("https://api.openalex.org/works/doi:"
                    + urllib.parse.quote(doi)
                    + "?select=id,title,is_retracted")
    if not data:
        return None
    # Some publishers only signal it by retitling the record.
    return bool(data.get("is_retracted")) or \
        (data.get("title") or "").upper().startswith("RETRACTED")


def unpaywall(doi):
    """Where a legal free full text lives, if one does."""
    if not doi or not CONTACT:
        return {}
    data = get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?"
                    + _q({"email": CONTACT}))
    if not data:
        return {}
    best = data.get("best_oa_location") or {}
    return {
        "open_access": bool(data.get("is_oa")),
        "oa_url": best.get("url_for_pdf") or best.get("url") or "",
    }


def s2_lookup(dois):
    """Batch citation + influence lookup. One call for up to 500 DOIs."""
    dois = [d for d in dois if d][:500]
    if not dois:
        return {}
    body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode()
    url = ("https://api.semanticscholar.org/graph/v1/paper/batch?"
           + _q({"fields": "externalIds,citationCount,influentialCitationCount,tldr"}))
    _throttle(url)
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}

    out = {}
    for item in data or []:
        if not item:
            continue
        doi = norm_doi((item.get("externalIds") or {}).get("DOI"))
        if not doi:
            continue
        tldr = (item.get("tldr") or {}).get("text") or ""
        out[doi] = {
            "citations": int(item.get("citationCount") or 0),
            "influential": int(item.get("influentialCitationCount") or 0),
            "tldr": clean(tldr),
        }
    return out
