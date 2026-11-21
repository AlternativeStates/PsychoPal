# PsychoPal — notes for Claude

Weekly literature/funding/events feed for Claudio Agnorelli (Centre for Psychedelic Research, Imperial). See README.md.

- Design rule: **scripts do retrieval, ranking and rendering**; Claude is optional, minimal-cost (~$3-4/yr) and fail-safe.
  `llm.enabled` (settings.yaml) is the single MASTER switch for all Claude use (off = zero API calls, exactly the scripted feed).
  Claude parts (decided Nov 2026): card text (`summarise.py`: Haiku 5.5, cached, direct calls since 21 Nov 2026 (`llm.use_batch: false`: ~2 min instead of up to 1 h), papers get `sections`
  Aim/Methods/Results/Conclusion + why_you; opportunity pages get status/dates/relevance), weekly capped re-rank and monthly taste
  digest from 👍/👎/★ (`prefs.py`, Haiku; bonus at most ±4 points via `papers.run(adjust=...)`), monthly web sweep
  (`monthly_sweep.py`, Sonnet 5.5, ≤6 searches, once per month via state/sweeps.jsonl). Shared helpers in `llm.py` (per-model
  `thinking` param: Haiku `disabled`, Sonnet 5.5 `between_tools`; cost accounting; `llm.monthly_budget_usd` soft cap).
  Any Claude failure = scripted text / script-only ranking, retried next week. Without Claude, paper cards get a scripted summary
  (`psychopal/abstracts.py`, recomputed every run, never cached). Bump `summarise.PROMPT_VERSION` when the prompt/schema changes.
  Deadline countdowns/strip/badges are off (`deadlines: false`). Keep new features scripted first.
- Entry point: `python -m psychopal.run_weekly` (flags: `--week-end`, `--start`, `--end`, `--no-llm`, `--llm-limit`,
  `--reuse-papers`, `--skip`). Editions: one per ISO week, filed under the week of the period's LAST day (`edition_window`);
  a re-run keeps an earlier start unless `--start` is given; max 28 days; per-week amounts scaled by `scale_for_period`;
  label "(so far)" when the period stops before Sunday. Cards shown in other editions never repeat (items.jsonl).
- Triggers (he asked for "not too many"; each automatic one fires once a week): (1) ONE GitHub cron Monday 05:17 UTC (fires
  hours late or not at all for this repo); (2) laptop nudge, Windows task "PsychoPal weekly nudge", Monday 07:30 only
  (StartWhenAvailable, retries 10 min × 1 h if offline) → `python -m psychopal.nudge` → force-push origin/main's tree to
  `weekly-nudge` → `nudge.yml` → weekly.yml with `only_if_missing=true`; (3) the app's ⟳ button (`site/refresh.js`) →
  workflow_dispatch with `end` (+ `start`) using the sync key (fine-grained PAT, Contents AND Actions read+write). "Done" =
  one edition from last Monday (or earlier) to last Sunday (or later): `nudge.covered()`, the `check` job and refresh.js use
  the same rule. Inputs reach the build step only as env vars. Keep input/branch names stable. No external timers (he
  found cron-job.org + a token too complicated).
  Each build saves the key-check verdict (no key shape, no workspace ID) in `state/claude_check.json`.
- Config lives in `config/` (queries.json = topics/weights; settings.yaml; watchlist YAMLs). Topics are ranked (Nov 2026):
  psychedelics > neuroimaging (incl. brain stimulation) > neuropsychopharm > bio_psychiatry > neurodynamics (merged with the old
  comp_neuro) > brain_body; quotas in settings `stream_quota`, ranking tilt in queries.json `scoring.stream_bonus`/`primary_stream`. State in `state/` (committed by CI).
- Site: `site/` static (feed: index.html/app.js/styles.css, sw.js = offline cache, network-first; archive/: index, search, graph, sync.js).
  Edition JSON has `alerts` (top banner), `health` (run notes) and optional `taste` (what Claude learned; box above Top 5). Streams "wider" and "tools" are side streams (never Top 5). Marks = localStorage `pp-marks`,
  synced to the `user-state` branch; never write that branch from automation.
- Windows dev: always `open(..., encoding="utf-8")`. Don't put regex backslashes in bash heredocs (they get mangled); use Edit/Write. Tests: `python -m pytest -q` (offline; LLM path uses a fake client).
- Derived from Patrick's neurodynamics-digest (C:\Users\claud\Desktop\Neuroimaging\Neurointegration\neurodynamics-digest).
