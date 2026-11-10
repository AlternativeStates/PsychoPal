# PsychoPal

A weekly, phone-friendly feed that keeps you on top of:

- **Papers** in 6 topics, in order of importance (papers per week, minimum–maximum):
  1. **Psychedelic research** (7–10): every aspect of the field, including clinical, preclinical, imaging, pharmacology and chemistry,
     experience and phenomenology, safety, epidemiology, policy and ethics. Any paper with a psychedelic in its title goes here.
  2. **Neuroimaging & brain stimulation** (4–7): technical EEG/MEG, fMRI, PET and MRI methods; TMS, TMS-EEG, tDCS/tACS, ultrasound, DBS.
  3. **Neuropsychopharmacology** (3–6): ketamine, stimulants (cocaine, amphetamine, methylphenidate), cannabis, serotonin PET and
     other psychopharmacology.
  4. **Biological psychiatry** (3–5): depression, addiction and PTSD, mainly preclinical (stress, fear and drug-seeking models),
     plus key human biology (biomarkers, genetics, post-mortem, imaging).
  5. **Neural dynamics & computational neuroscience** (2–5): EEG complexity, criticality, waves, aperiodic activity, information
     theory, whole-brain and neural-mass models, DCM, computational psychiatry.
  6. **Brain–body coupling** (2–4): heart, breath and gut rhythms and the brain; interoception.

  About 25 a week: a pinned "Top 5 for you", then each topic's minimum (one paper per sub-area first, e.g. ketamine, stimulants,
  cannabis), then the best of the rest. Higher topics get a small ranking bonus, so the Top 5 also leans towards psychedelics. A variety
  rule skips near-duplicates of a paper already picked; case reports, narrative reviews in weaker journals, oncology papers (outside
  psychedelics) and acupuncture/herbal-medicine papers rank lower.
- **Wider science** (low priority): 4 cards a week from broad journals (Nature, Science, Cell, PNAS, eLife…) and papers or preprints
  from any field that many people are posting about on Bluesky. Ranked by journal, attention and brain/behaviour topics, not by
  similarity to your work. Never in the Top 5.
- **Funding** for your career stage (Wellcome, MRC, UKRI, Royal Society, Leverhulme, MSCA, ERC, EMBO, HFSP, BBRF, Imperial…)
  plus new UKRI calls.
- **Conferences** (Psychedelic Science, INSIGHT, ICPR, ASSC, OHBM, IPEG, NRM, ECNP…) and **training** (SPM, FSL, TMS-EEG, CPC Zurich…).
  A card appears when an entry is new or its web page changes. Deadline countdowns are switched off (`deadlines: false` in
  `config/settings.yaml`): deadlines live in your own calendar.
- **Tools & methods**: new releases of neuroimaging software you use (one card per tool), brand-new GitHub repositories under neuro
  topics (ranked by stars), papers presenting software (including JOSS, the Journal of Open Source Software) and papers presenting
  new analysis methods (NeuroImage, Imaging Neuroscience, J Neurosci Methods, Nature Methods, HBM…). A 👍 or ☆ on a tool card with
  a GitHub repo adds that repo to the release watch.
- Extras: newly registered psychedelic trials, 1–2 "Catch-up" classics, and 🔥 Buzz (papers being discussed on Bluesky).

Each item is a short card, like a post. A paper card summarises its abstract in a standard structure, **Aim · Methods · Results ·
Conclusion**, one or two sentences each, plus a small "Picked for" line that says why the script chose it (keywords, modality, the
closest of your own papers). You can 👍, 👎 (hide), ☆ save and share cards. The ✳ button
sends a paper to the Claude app (through the share menu) with a ready-made prompt asking for design, sample, results and limitations.
All your marks feed back into next week's ranking. The **Saved** tab exports all saved papers as one `.ris` file for Zotero.
The app works offline for the weeks you have opened (and the latest three). A warning banner appears at the top when something
went wrong that week (e.g. the literature databases could not be reached). There is also a searchable **archive** with
filters, a graph view and a literature search page. It is adapted from Patrick's Neurodynamics Digest.

## How it works (free: no API key needed)

Every Monday at 05:30 UTC, GitHub Actions runs a set of Python scripts. **No AI model is used**: Claude support is built in but
switched off (see "Optional: Claude" below).

1. `psychopal/papers.py` queries PubMed, Europe PMC, OpenAlex, bioRxiv/medRxiv and arXiv, then removes duplicates. It scores papers
   by keyword groups (`config/queries.json`), imaging modality, named researchers, journal, and similarity to **your own papers**
   and your 👍. No LLM is used.
2. `psychopal/extras.py` adds trials from ClinicalTrials.gov, Catch-up classics from OpenAlex (one per topic), and Buzz from Bluesky
   and Hacker News. `psychopal/wider.py` picks the Wider science cards.
3. `psychopal/opportunities.py` re-reads every watched funding, conference and training page and spots changes. It also adds new UKRI calls.
   Pages that block automated checks are marked `no_fetch: true` in the YAML. The card text is the `notes` written in the YAML.
4. `psychopal/tools_watch.py` collects new GitHub releases, new neuro repositories and new JOSS papers.
5. `psychopal/abstracts.py` writes the paper summaries. Structured abstracts (labelled Background / Methods / Results /
   Conclusions…) are mapped onto the four parts directly. For unstructured ones, each sentence is scored on cue phrases ("here we",
   "we recruited", "p < 0.05", "these findings suggest") and position, and the best labelling in reading order is chosen.
   Limitation sentences are left out. Abstracts under 60 words keep their plain text.
6. `psychopal/build_site.py` and `build_archive.py` write the static site in `site/`, which is deployed to GitHub Pages.

**Cost:** nothing. GitHub hosting and Actions are free.

### Optional: Claude (switched off)
The code for two Claude features is kept, ready to switch on later:
- `psychopal/summarise.py`: Claude writes the card text (2–3 sentences plus "Why it matters for you") for new items only, in one
  batch (Sonnet 5.5, 50% batch discount), cached for good. Watched pages also get their deadline and dates read.
- `psychopal/monthly_sweep.py`: on the first Monday of each month, one Claude call with web search looks for funding calls,
  conferences and courses not on your lists yet.

To switch on: in `config/settings.yaml` set `llm: enabled: true` (and `sweep: enabled: true` for the sweep), then do steps C and D
of the setup below. Expected cost about $8–10 a year.

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

### C. (Only if you switch Claude on) Get a Claude API key (Claude Console, platform.claude.com)
- **If your lab has a Claude Team plan:** the plan includes monthly API credits ($20 per Standard seat, $100 per Premium seat,
  pooled, max $500). An Owner claims them on claude.ai › **Organization settings › Billing › API credits**, by linking a Console
  organization (you can create one during the claim). The link can't be changed later, so pick the lab's Console org.
  Enterprise plans are not eligible; buy credits on the Console **Billing** page instead (≈$10 lasts about a year).
- In the Console (as Admin): **Settings › Workspaces › Create workspace** named `PsychoPal`; select it › **Spend limits** ›
  e.g. $5/month.
- **Settings › API keys › Create key**: name `PsychoPal GitHub`, Linked account = you (or a service account), **Workspace =
  PsychoPal**, expiration **1 year** (or the longest option short of "never"). Copy the `sk-ant-api…` key (shown once).
  Put its expiry date in `config/settings.yaml` › `llm.key_expires`: the feed then shows a warning 3 weeks before it runs out.
  The key lives only in GitHub's encrypted Secrets, never in the public code; the workspace spend limit caps any misuse.
  A key **not** tied to a workspace needs the workspace ID too: add it as a second secret `ANTHROPIC_WORKSPACE_ID` (`wrkspc_…`).

### D. GitHub secrets and variables
Repo › **Settings** › sidebar *Security* › **Secrets and variables › Actions**. Optional: tab **Variables** › `PSYCHOPAL_MAILTO`
(contact email for PubMed/OpenAlex); secret `NCBI_API_KEY` (free, from your NCBI account). Only if Claude is switched on:
**New repository secret** › Name `ANTHROPIC_API_KEY`, Secret = the key › **Add secret**.

### E. First run
Repo › **Actions** tab › **Weekly PsychoPal** (left list) › **Run workflow** › `week_end` = `2026-11-08` › **Run workflow**.
Wait 10–40 min, open the run: the **summary** shows the cards ("Claude key: OFF" while Claude is switched off). The `deploy`
job shows the site address.

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
| Topics, keywords, weights, journals, named researchers; Wider science journals, tiers and Bluesky queries (`wider`). Within a topic, keyword groups are listed in priority order: each topic's minimum takes one paper per sub-area in that order (e.g. ketamine, stimulants, cannabis) | `config/queries.json` |
| Papers per week, papers per topic (`stream_quota`, in order of importance), variety threshold (`max_similarity`), Wider science cards, deadlines on/off, Claude on/off, model, caps and key expiry | `config/settings.yaml` |
| Topic ranking bonus (`scoring.stream_bonus`) and the "psychedelic in the title" rule (`scoring.primary_stream`) | `config/queries.json` |
| Summary parts and their length (words per part, `LIMITS`) | `psychopal/abstracts.py` |
| Who you are (what Claude uses for "Why it matters for you", if switched on) | `config/profile.md` |
| Eligibility facts | `config/profile.yaml` |
| Funding / conferences / training lists | `config/funding.yaml`, `conferences.yaml`, `training.yaml` (add `disabled: true` to stop tracking an entry; `no_fetch: true` + `deadline:` for sites that block automated checks) |
| Software to watch; GitHub topics for new repositories | `config/tools.yaml` |
| Your own papers (the relevance seed) | regenerate with `python scripts/seed_profile.py` after you publish |
| Archive filter chips | `config/tag_map.json` |

## Running it on your PC

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m psychopal.run_weekly                     # last full week
$env:ANTHROPIC_API_KEY="sk-ant-..."; .\.venv\Scripts\python -m psychopal.run_weekly --llm-limit 5   # (Claude on) try Claude on 5 cards
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
