# PsychoPal

A weekly, phone-friendly feed that keeps you on top of:

- **Papers** in 8 streams: psychedelic research, neuropsychopharmacology, neuroimaging, computational neuroscience, neuroscience,
  biological psychiatry, brain–body coupling and neurodynamics. About 25 a week: a pinned "Top 5 for you", then at least 2 per stream.
- **Funding** for your career stage (Wellcome, MRC, UKRI, Royal Society, Leverhulme, MSCA, ERC, EMBO, HFSP, BBRF, Imperial…)
  plus new UKRI calls.
- **Conferences** (Psychedelic Science, INSIGHT, ICPR, ASSC, OHBM, IPEG, NRM, ECNP…) and **training** (SPM, FSL, TMS-EEG, CPC Zurich…),
  each with a deadline countdown.
- **Tools**: new releases of neuroimaging software you use, and new papers presenting software (including from JOSS, the Journal of Open Source Software).
- Extras: newly registered psychedelic trials, 1–2 "Catch-up" classics, and 🔥 Buzz (papers being discussed on Bluesky).

Each item is a short card, like a post: 2–3 sentences plus "Why it matters for you". You can 👍, 👎 (hide), ☆ save and share cards.
All your marks feed back into next week's ranking. There is also a searchable **archive** with filters, a graph view and a literature
search page. It is adapted from Patrick's Neurodynamics Digest.

## How it works (and why it is cheap)

Every Monday at 05:30 UTC, GitHub Actions runs a set of Python scripts:

1. `psychopal/papers.py` queries PubMed, Europe PMC, OpenAlex, bioRxiv/medRxiv and arXiv, then removes duplicates. It scores papers
   by keyword groups (`config/queries.json`), imaging modality, named researchers, journal, and similarity to **your own papers**
   and your 👍. No LLM is used.
2. `psychopal/extras.py` adds trials from ClinicalTrials.gov, Catch-up classics from OpenAlex, and Buzz from Bluesky and Hacker News.
3. `psychopal/opportunities.py` re-reads every watched funding, conference and training page and spots changes. It also adds new UKRI calls.
4. `psychopal/tools_watch.py` collects new GitHub releases and new JOSS papers.
5. `psychopal/summarise.py` is **the only Claude call**. It sends only items that have **never been written before** to Claude
   (Sonnet 5.5) in one batch, which costs 50% less. Card text is cached for good. For watched pages, Claude also reads the deadline
   and dates. If Claude is unavailable, cards fall back to sentences from the abstract and the site still publishes.
6. `psychopal/build_site.py` and `build_archive.py` write the static site in `site/`, which is deployed to GitHub Pages.

On the first Monday of each month, `psychopal/monthly_sweep.py` makes one extra Claude call with web search. It looks for funding
calls, conferences and courses that are not on your lists yet, and adds them as "NEW ON YOUR RADAR".

**Expected cost:** about $0.10 a week for card text, plus about $0.30 a month for the sweep, so **about $8–10 a year**.
GitHub hosting and Actions are free. Token usage is logged in `state/stats.json` and `state/runs.jsonl`.

## One-time setup (≈15 minutes)

1. **Publish the repo.** In GitHub Desktop: *File › Add local repository* › `C:\Users\claud\Desktop\PsychoPal`, then *Publish repository*.
   Name it `PsychoPal` and untick "Keep this code private" (free GitHub Pages needs a public repo).
2. **Turn on Pages.** On github.com, open the repo › *Settings › Pages › Source*: **GitHub Actions**.
3. **Add your Claude API key.** Create a key at [console.anthropic.com](https://console.anthropic.com/) (this is separate from a
   Claude.ai subscription; about $10 of credit lasts roughly a year). Then go to the repo › *Settings › Secrets and variables ›
   Actions › New repository secret*, with name `ANTHROPIC_API_KEY`.
   Optional:
   - a *variable* `PSYCHOPAL_MAILTO`: a contact email that PubMed and OpenAlex ask polite clients to send;
   - a secret `NCBI_API_KEY`: a free key from your NCBI account, for higher PubMed rate limits.
4. **First run.** Open the *Actions* tab › **Weekly PsychoPal** › *Run workflow*. Leave the fields empty, or set `week_end` to
   `2026-11-08` to rebuild the preview week with Claude text. It takes 10–40 minutes; most of that is waiting for the batch.
5. **Phone.** Open `https://alternativestates.github.io/PsychoPal/`, then *Share › Add to Home Screen* (iPhone) or
   *⋮ › Install app* (Android).
6. **Sync your likes across devices** (optional, but it makes the ranking learn from you):
   1. Tap the "local only · connect sync" pill at the bottom right and follow the link.
   2. Create a fine-grained token with access to **only** this repo and *Contents: Read and write*.
   3. Paste it in. Do this once per device.

   Your marks are saved to the `user-state` branch, and the weekly run reads them.
7. **Fill in your eligibility.** In `config/profile.yaml`, add your PhD award date, nationality and the countries you have lived in
   recently. Until then, fellowships with eligibility limits show a "check eligibility" note instead of being filtered.

## Tuning (edit the files on github.com or locally; no code changes needed)

| What | File |
|---|---|
| Topics, keywords, weights, journals, named researchers | `config/queries.json` |
| Papers per week, stream minimums, countdown weeks, Claude model and caps | `config/settings.yaml` |
| Who you are (what Claude uses for "Why it matters for you") | `config/profile.md` |
| Eligibility facts | `config/profile.yaml` |
| Funding / conferences / training lists | `config/funding.yaml`, `conferences.yaml`, `training.yaml` (add `disabled: true` to stop tracking an entry) |
| Software to watch | `config/tools.yaml` |
| Your own papers (the relevance seed) | regenerate with `python scripts/seed_profile.py` after you publish |
| Archive filter chips | `config/tag_map.json` |

## Running it on your PC

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m psychopal.run_weekly --no-llm            # last full week, no Claude
$env:ANTHROPIC_API_KEY="sk-ant-..."; .\.venv\Scripts\python -m psychopal.run_weekly --llm-limit 5   # try Claude on 5 cards
.\.venv\Scripts\python -m http.server 8000 --directory site       # then open http://localhost:8000
.\.venv\Scripts\python -m psychopal.opportunities --check         # are all watched pages reachable?
```

Other flags: `--week-end YYYY-MM-DD` (rebuild a given week; re-running a week is safe), `--reuse-papers` (re-score the cached
search after editing `queries.json`), `--skip buzz,catchup,...`.

## Robustness

- Each source and each stage fails on its own. A failure appears as a short "Run notes" line at the bottom of the week, and the
  edition still publishes.
- If a weekly run fails completely, GitHub emails you.
- Nothing is shown twice: every card ever shown is kept in `state/items.jsonl`.
- 👎 on any card hides it for good, and similar papers rank lower.
- Re-running a week rebuilds that week only; it never duplicates cards.

## Privacy

The repo and site are public, but the site is hidden from search engines (`noindex`, `robots.txt`). Your config and your
likes/saves (as item ids, on the `user-state` branch) are visible to anyone who opens the repo. Your sync token stays only in your
browser.
