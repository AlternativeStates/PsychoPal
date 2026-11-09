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

## One-time setup (≈20 minutes)

### A. Publish the repo (GitHub Desktop)
1. GitHub Desktop › **File › Add local repository…** › *Choose…* › `C:\Users\claud\Desktop\PsychoPal` › **Add repository**.
2. Check the account: **File › Options › Accounts** must show the GitHub account you want (e.g. `AlternativeStates`).
3. Click **Publish repository** (top bar). Name `PsychoPal`, **untick "Keep this code private"**, Organization: *None*
   (your personal account) › **Publish repository**.
4. **Repository › View on GitHub** (Ctrl+Shift+G) opens the repo page in your browser.
   If the account or repo name differs from `AlternativeStates/PsychoPal`, edit `github_repo` and `site_url` in
   `config/settings.yaml`, then commit and push.

### B. Turn on GitHub Pages
On the **repo page** (not your profile settings): the **⚙ Settings** tab at the right of the tab row (*Code · Issues ·
Pull requests · Actions · …*). Hidden on a narrow window? Click **…** at the end of the row. Left sidebar › section
*Code and automation* › **Pages** › *Build and deployment* › **Source: GitHub Actions**. Do not pick a suggested workflow:
the repo already has one (`.github/workflows/weekly.yml`).

### C. Get a Claude API key (Claude Console, platform.claude.com)
- **If your lab has a Claude Team plan:** the plan includes monthly API credits ($20 per Standard seat, $100 per Premium seat,
  pooled, max $500). An Owner claims them on claude.ai › **Organization settings › Billing › API credits**, by linking a Console
  organization (you can create one during the claim). The link can't be changed later, so pick the lab's Console org.
  Enterprise plans are not eligible; buy credits on the Console **Billing** page instead (≈$10 lasts about a year).
- In the Console (as Admin): **Settings › Workspaces › Create workspace** named `PsychoPal`; select it › **Spend limits** ›
  e.g. $5/month.
- **Settings › API keys › Create key**: name `PsychoPal GitHub`, Linked account = you (or a service account), **Workspace =
  PsychoPal**, expiration *Never* if offered, else the longest allowed. Copy the `sk-ant-api…` key (shown once).
  A key **not** tied to a workspace needs the workspace ID too: add it as a second secret `ANTHROPIC_WORKSPACE_ID` (`wrkspc_…`).

### D. Add the key to GitHub
Repo › **Settings** › sidebar *Security* › **Secrets and variables › Actions** › **New repository secret** › Name
`ANTHROPIC_API_KEY`, Secret = the key › **Add secret**. Optional: tab **Variables** › `PSYCHOPAL_MAILTO` (contact email for
PubMed/OpenAlex); secret `NCBI_API_KEY` (free, from your NCBI account).

### E. First run
Repo › **Actions** tab › **Weekly PsychoPal** (left list) › **Run workflow** › `week_end` = `2026-11-08` › **Run workflow**.
Wait 10–40 min, open the run: the **summary** shows "Claude key: OK", the cards and the token cost. The `deploy` job shows the
site address.

### F. Phone, sync and eligibility
- Open `https://<your-user-name-in-lowercase>.github.io/PsychoPal/` › iPhone: Share › **Add to Home Screen**; Android: ⋮ ›
  **Install app**.
- Sync likes across devices: tap the "local only · connect sync" pill and follow it (fine-grained token, only this repo,
  *Contents: Read and write*), once per device.
- Fill in `config/profile.yaml` (PhD date, nationality, recent countries) so fellowships you can't apply for are hidden.

Check a key locally: `$env:ANTHROPIC_API_KEY="sk-ant-..."; .\.venv\Scripts\python -m psychopal.check_claude`

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
