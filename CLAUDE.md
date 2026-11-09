# PsychoPal — notes for Claude

Weekly literature/funding/events feed for Claudio Agnorelli (Centre for Psychedelic Research, Imperial). See README.md.

- Design rule: **scripts do retrieval, ranking and rendering; Claude only writes card text** (`psychopal/summarise.py`, one cached
  Message Batch per week) plus the monthly web-search sweep (`psychopal/monthly_sweep.py`). Keep new features scripted first.
- Entry point: `python -m psychopal.run_weekly` (flags: `--week-end`, `--no-llm`, `--llm-limit`, `--reuse-papers`, `--skip`).
- Config lives in `config/` (queries.json = topics/weights; settings.yaml; watchlist YAMLs). State in `state/` (committed by CI).
- Site: `site/` static (feed: index.html/app.js/styles.css; archive/: index, search, graph, sync.js). Marks = localStorage `pp-marks`,
  synced to the `user-state` branch; never write that branch from automation.
- Windows dev: always `open(..., encoding="utf-8")`. Tests: `python -m pytest -q` (offline; LLM path uses a fake client).
- Derived from Patrick's neurodynamics-digest (C:\Users\claud\Desktop\Neuroimaging\Neurointegration\neurodynamics-digest).
