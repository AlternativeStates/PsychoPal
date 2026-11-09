# PsychoPal — notes for Claude

Weekly literature/funding/events feed for Claudio Agnorelli (Centre for Psychedelic Research, Imperial). See README.md.

- Design rule: **scripts do retrieval, ranking and rendering**. Claude is **switched off** (settings.yaml `llm.enabled` and
  `sweep.enabled` false; the user chose to run without an API key, Nov 2026) but its code is kept: card text (`psychopal/summarise.py`,
  one cached Message Batch per week) and the monthly web-search sweep (`psychopal/monthly_sweep.py`). Without Claude, paper cards get
  a scripted Aim/Methods/Results/Conclusion summary (`psychopal/abstracts.py`, field `sections`, recomputed every run, never cached).
  Deadline countdowns/strip/badges are off (`deadlines: false`). Keep new features scripted first.
- Entry point: `python -m psychopal.run_weekly` (flags: `--week-end`, `--no-llm`, `--llm-limit`, `--reuse-papers`, `--skip`).
- Config lives in `config/` (queries.json = topics/weights; settings.yaml; watchlist YAMLs). State in `state/` (committed by CI).
- Site: `site/` static (feed: index.html/app.js/styles.css, sw.js = offline cache, network-first; archive/: index, search, graph, sync.js).
  Edition JSON has `alerts` (top banner) and `health` (run notes). Streams "wider" and "tools" are side streams (never Top 5). Marks = localStorage `pp-marks`,
  synced to the `user-state` branch; never write that branch from automation.
- Windows dev: always `open(..., encoding="utf-8")`. Don't put regex backslashes in bash heredocs (they get mangled); use Edit/Write. Tests: `python -m pytest -q` (offline; LLM path uses a fake client).
- Derived from Patrick's neurodynamics-digest (C:\Users\claud\Desktop\Neuroimaging\Neurointegration\neurodynamics-digest).
