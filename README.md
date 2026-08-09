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
python fetch.py                 # last 3 days (config default)
python fetch.py --days 7        # wider net, e.g. after a weekend
python fetch.py --topic medicine
python fetch.py --top 15
```

Outputs `out/YYYY-MM-DD.md` (readable shortlist) and `out/YYYY-MM-DD.json`
(what the workspace loads).

## What it does

```
Europe PMC  (MEDLINE + PMC + preprints)     arXiv (cs.AI/CL/LG/CY)
        │  two sweeps:                              │  newest-first paging
        │  · topic keywords in title/abstract       │  until past the window
        │  · anything in a tier-4/5 journal         │
        └──────────────────┬────────────────────────┘
                     dedupe by DOI/title
                           │
                    hard excludes  (protocols, errata, editorials, retractions)
                           │
                    heuristic score
                           │
                     top N → shortlist.md
                           │
                    ☐ shoot  ☐ hold  ☐ kill     ← you, every morning
```

Last run: 2,381 unique papers → 10 shortlisted.

## How scoring works

Deliberately dumb and transparent — every shortlist entry prints the exact
reasons it ranked where it did, so you can argue with it and tune `config.json`.

| Signal | Effect |
|---|---|
| Journal tier | +3 to +5 (exact match; substring only for multi-word titles) |
| Study design | meta-analysis / RCT +6, cohort +3, case report −3, mouse study −5 |
| Sample size | `log10(n)`, capped at +5 |
| Topic keyword hits | up to +3; −1 if none |
| Preprint | −2 and a loud ⚠️ in the output |
| Citations | up to +2 |
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
fetch.py        discovery + scoring          config.json   all tuning knobs
server.py       local API + static server    out/          shortlists per day
web/            the workspace UI             data/         your saved work
build_demo.py   generates docs/              docs/         the public demo
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
