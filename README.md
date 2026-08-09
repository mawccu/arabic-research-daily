# Arabic Research Daily

**Live demo → https://mawccu.github.io/arabic-research-daily/**
(real papers, real scoring; fetching and full text need the local app)

The part of the pipeline that decides whether the whole thing is 8/10 or 5/10:
finding the one paper per day that's actually worth an episode, without you
reading 2,000 abstracts — then reading, annotating and scripting it in one place.

## Start the workspace

```
python server.py
```

Then open **http://localhost:8420**. Everything runs from there — fetching,
reading, highlighting, scripting, verdicts.

No API keys, no `pip install`, no build step. Stdlib + vanilla JS.
Binds to localhost only; nothing leaves your machine except the paper lookups.

## The home screen

The entry point at `/` is a hero page with one button — **Generate a research** —
which runs the pipeline with the log streaming underneath, plus days-back,
shortlist-size and topic controls. Finished runs appear below as cards showing
what was scanned, the top-ranked paper, and how many you've decided on. Click a
card to open it; the `⋯` menu gives you open, retitle, export JSON, open the
Markdown, or delete the run (deleting removes the shortlist files only — your
highlights, scripts and verdicts in `data/` are kept).

## The workspace

Cards open `/workspace?date=YYYY-MM-DD`. Above the panes is an action bar:

| Button | What it does |
|---|---|
| **Translate to Arabic** | Sends your highlights — or the whole abstract if you haven't highlighted anything — to Claude and appends the Arabic to the Script tab |
| **Arabic outline** | Same, but returns the four-section episode skeleton (الفكرة الأساسية / ماذا وجدت الدراسة / كيف اختبروها / ما الذي لا تعنيه هذه النتيجة) |
| **Copy citation** | Authors, year, title, journal, DOI — to the clipboard |
| **Export script** | Downloads the script as Markdown with the source and preprint warning attached |
| **Teleprompter** | Full-screen scrolling view of the script for recording — space to play/pause, adjustable speed and type size, Esc to exit |
| **Print** | Prints just the document pane |

**Translation is the one feature that needs an outside service.** It calls the
Claude API from your local server:

```
pip install anthropic
setx ANTHROPIC_API_KEY "sk-ant-..."     # then restart the server
```

Without the package or the key, the button says exactly what's missing instead
of failing quietly. It's told to keep every number, sample size and effect size
exactly as given and never to strengthen a claim — but it is a machine
translation of a paper you are responsible for. Read it before you record.

## The three panes

**Left — the shortlist.** Search, and filter by verdict, source, study design,
minimum sample size, peer-reviewed-only, tier 4–5 journals. Sort by score, date,
sample size, citations, or title. Each card shows its score, journal, and pills
for preprint / tier / n / verdict / notes.

**Centre — the document.** Two tabs:
- *Paper* — the paper set as a page. Structured abstracts get split back into
  Background / Methods / Results / Conclusions. **Load full text** pulls the
  complete open-access text when the paper is in PMC (button tells you when it's
  paywalled instead of failing silently). Select any text and hit a colour:
  yellow = key finding, green = method, purple = limitation, red = caution.
- *Script* — a Word-style editor, **right-to-left by default** for Arabic. Bold,
  italic, headings, lists, quotes. Live word count with an estimated read time at
  ~140 words/minute. **↧ highlights** drops every highlight you made into the
  script as block quotes, so the script is built from the paper rather than memory.

**Right — four tabs:**
- *Sources* — the full record, one-click links to DOI / PubMed / PMC / Scholar,
  and the paper's own reference list pulled from Europe PMC.
- *Highlights* — every highlight collected; click one to jump to it in the paper.
- *Scoring* — exactly why this paper ranked where it did, line by line.
- *Checks* — the pre-shoot checklist. Persisted per paper.

**Bottom** — Shoot / Hold / Kill. Killed papers dim in the list.

Everything autosaves to `data/state.json` and survives restarts and re-fetches
(papers are keyed by DOI, so the same paper keeps your work across runs).

**Keyboard:** `j`/`k` next/previous paper · `1`/`2`/`3` shoot/hold/kill ·
`Ctrl+S` force save · `Esc` close dialogs. Drag the pane edges to resize.
`◐` toggles dark mode.

## Command line

The fetcher runs standalone too, if you'd rather cron it:

```
python fetch.py                        # last 3 days, every enabled source
python fetch.py --days 7               # wider net, e.g. after a weekend
python fetch.py --quick                # skip the slow per-keyword passes
python fetch.py --topic medicine
python fetch.py --sources openalex,europepmc
python fetch.py --top 15
```

A full run makes ~37 requests across six APIs and takes a few minutes;
`--quick` cuts it to about one. Every source is throttled to its published
rate limit, so it's a well-behaved client, not a scraper.

Outputs `out/YYYY-MM-DD.md` (readable shortlist) and `out/YYYY-MM-DD.json`
(what the workspace loads).

## What it does

It works the way a person would with unlimited patience: ask every index that
will answer, ask each one several *different* questions, follow citation
threads forward, then cross-check before ranking.

```
   Europe PMC        OpenAlex         Crossref      arXiv   medRxiv   S2
  ┌───────────┐   ┌────────────┐   ┌───────────┐     │        │       │
  │ keywords  │   │ newest     │   │ per-topic │     │ cats   │ new   │ relevance
  │ tier 4/5  │   │ most-cited │   │ queries   │     │        │       │ ranking
  │ RCT/meta  │   │ per-keyword│   │           │     │        │       │
  └─────┬─────┘   └──────┬─────┘   └─────┬─────┘     │        │       │
        │                │               │           │        │       │
        │      citation-chase: what already cites the biggest hit?     │
        └────────────────┴───────────────┴───────────┴────────┴───────┘
                                  │
                    merge on DOI — a paper found by 3 indexes
                    keeps the richest field from each
                                  │
                    score  →  verify the top 30:
                              · retraction check (OpenAlex + Crossref)
                              · citation + influence (Semantic Scholar)
                              · free full text (Unpaywall)
                                  │
                    drop anything retracted, backfill, re-rank
                                  │
                       ☐ shoot  ☐ hold  ☐ kill   ← you, every morning
```

Last run: **37 searches → 8,357 records → 6,331 unique papers → 10 shortlisted.**

## Sources

| Source | Covers | How it's queried |
|---|---|---|
| **Europe PMC** | MEDLINE, PMC, preprints | keyword sweep · tier-4/5 journal sweep · strong-design sweep (RCT / meta-analysis / systematic review) |
| **OpenAlex** | 250M+ works, every discipline | newest · most-cited-in-window · one search per topic keyword · citation chasing |
| **Crossref** | every registered DOI | per-keyword bibliographic queries; catches papers subject indexes haven't picked up yet |
| **arXiv** | cs.AI / CL / LG / CY | newest-first paging until past the window |
| **medRxiv / bioRxiv** | preprints | full window, and flags any that a journal has since published |
| **Semantic Scholar** | 200M+ papers | its own relevance ranking, plus batch citation/influence lookup |
| **Unpaywall** | OA locations | where a legal free full text lives |

**Why not Google Scholar.** It has no API, its terms forbid automated access,
and it enforces that with CAPTCHAs and IP bans — anything built on it breaks
within days. Scholar is mostly an *index of* sources that do have proper APIs;
OpenAlex is the open successor to Microsoft Academic and covers the same ground,
queryable in ways Scholar never allowed. Together with Crossref and Semantic
Scholar the coverage is comparable and the tool doesn't break.

Set `contact_email` in `config.json` to enter the polite pools at OpenAlex,
Crossref and Unpaywall — identified callers get higher rate limits, and
Unpaywall requires it. Semantic Scholar's anonymous pool rate-limits hard;
when a pass is throttled the run says so and carries on.

Add a discipline by adding a topic to `config.json` with an OpenAlex concept
id — `psychology` and `biology` are already there, switched off.

## How scoring works

Deliberately dumb and transparent — every shortlist entry prints the exact
reasons it ranked where it did, so you can argue with it and tune `config.json`.

| Signal | Effect |
|---|---|
| Journal tier | +3 to +5 (exact match; substring only for multi-word titles) |
| Study design | meta-analysis / RCT +6, cohort +3, case report −3, mouse study −5 |
| Sample size | `log10(n)`, capped at +5 |
| Topic keyword hits | up to +3; −1 if none |
| **Found by N indexes** | **up to +3 — independent corroboration that the record is sound** |
| Preprint | −2 and a loud ⚠️; waived if a journal has since published it |
| Citations / influential citations | up to +2 each |
| Correction issued | −1.5 and a flag |
| Retracted | **excluded outright** |
| Title hooks | "largest", "no benefit", "reversed" etc. +1 to +1.5 |

Tune everything in `config.json` — journal tiers, design weights, keywords,
hard excludes. Nothing is hardcoded in `fetch.py`. The **Config** button in the
workspace edits the same file; changes apply on the next fetch.

## What it deliberately does NOT do

It does not decide what's true, and it doesn't write the episode. It hands you
10 candidates with their reasoning exposed. The ranking is based on **abstracts**,
which routinely oversell. Before you shoot:

1. Open the actual paper, not the abstract.
2. Check what the control group got and how long follow-up ran.
3. Find the limitations section — that's usually where the episode's honest
   ending comes from.
4. Every claim in the script should map to a line you can point to in the PDF.

A ⚠️ PREPRINT flag means not peer reviewed. If you shoot it, say so on camera.

## Known limits

- Europe PMC stops at the page cap (10 × 200) per sweep; it warns when it hits it.
  Recent-days coverage is fine, long `--days` windows will truncate.
- arXiv rate-limits hard (429s); the client sleeps 3s between pages and backs off.
- Sample-size extraction is regex over the abstract — treat `n≈` as a hint, not a fact.
- Medicine and AI only so far. Adding psychology, economics, climate = a new
  entry in `config.json` `topics` (Europe PMC covers life sciences; other fields
  will need a Crossref or OpenAlex source function).
- **Full text only works for open-access papers in PMC.** Most high-tier medical
  papers are paywalled — none of the current top 10 had open full text. For those
  you get the abstract in the app and follow the DOI link for the PDF.
- Highlights are stored as saved HTML of the reading pane. Loading full text
  replaces that view, so it asks before discarding existing highlights.

## Files

```
sources.py      one adapter per index        config.json   all tuning knobs
fetch.py        harvest + merge + score      out/          shortlists per day
server.py       local API + static server    data/         your saved work
web/index.html  home screen                  docs/         the public demo
web/workspace.html  reader + script editor
build_demo.py   generates docs/
```

## Updating the public demo

`docs/` is a static build of `web/` with the latest shortlist baked in. When
there's no Python backend, `app.js` answers its own API calls from that data and
saves to `localStorage`, so the interface is fully usable — it just can't fetch,
load full text, or share state between machines. The page says so at the top.

```
python fetch.py            # get a fresh shortlist
python build_demo.py       # regenerate docs/
git add -A && git commit -m "rebuild demo" && git push
```

`web/` stays the single source of truth; never edit `docs/` by hand.

## Next stages (not built yet)

- Arabic first-draft generation from the highlights you selected
- Episode store + public references page
- Landing page + subscriptions
