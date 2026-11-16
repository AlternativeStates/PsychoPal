"""Shared Claude helpers (no API calls here): the thinking setting per model, usage and cost accounting, the monthly budget, and
the once-a-month rule for the web sweep. Prices: Anthropic pricing page, Nov 2026 (prompts up to 100k tokens)."""
import datetime as dt, os
from . import common

PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}   # $/MTok in, out
SONNET = PRICES["claude-sonnet-5-5"]
SEARCH_USD = 0.01          # web search: $10 per 1,000 searches (no batch discount)

def is_off(mode):
    """YAML reads a bare `off` as False, so accept False/None/'off'/'none' as 'no extended thinking'."""
    return mode in (False, None) or str(mode).strip().lower() in ("off", "none", "false", "no", "disabled")

def thinking_param(model, mode="off", effort="low"):
    """The request's `thinking` value, or None to leave it out (= the model's default, adaptive thinking).
    Haiku 5.5 turns thinking off with {"type": "disabled"} (effort high or lower); Sonnet 5.5 rejects that and uses "between_tools"
    instead; Opus 5.5 / Fable / Mythos cannot turn it off."""
    if not is_off(mode):
        return None
    m = model or ""
    if m.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}
    if m.startswith(("claude-opus-5-5", "claude-fable", "claude-mythos")):
        return None
    return None if effort in ("xhigh", "max") else {"type": "disabled"}

def price(model):
    for k, v in PRICES.items():
        if (model or "").startswith(k):
            return v
    return SONNET            # unknown model: assume the dearer price, so the budget errs on the safe side

def new_usage():
    return {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0, "web_searches": 0}

def usage_of(msg):
    """Token and search counts of one API response (missing fields count as 0)."""
    u, out = getattr(msg, "usage", None), new_usage()
    if u is None:
        return out
    g = lambda o, k: (getattr(o, k, 0) or 0) if o is not None else 0
    out["input_tokens"] = g(u, "input_tokens")
    out["output_tokens"] = g(u, "output_tokens")
    out["cache_write"] = g(u, "cache_creation_input_tokens")
    out["cache_read"] = g(u, "cache_read_input_tokens")
    out["web_searches"] = g(getattr(u, "server_tool_use", None), "web_search_requests")
    return out

def cost_usd(model, u, batch=False):
    pin, pout = price(model)
    tok = (u.get("input_tokens", 0) * pin + u.get("cache_write", 0) * pin * 1.25 + u.get("cache_read", 0) * pin * 0.1
           + u.get("output_tokens", 0) * pout) / 1e6
    return round(tok * (0.5 if batch else 1.0) + u.get("web_searches", 0) * SEARCH_USD, 8)

def account(acc, msg, model, batch=False):
    """Add one response's usage and cost to the running totals in `acc` (a stats dict)."""
    d = usage_of(msg)
    for k, v in d.items():
        acc[k] = acc.get(k, 0) + v
    acc["cost_usd"] = round(acc.get("cost_usd", 0.0) + cost_usd(model, d, batch), 8)
    return acc

def month_spend(today=None):
    """Claude spend so far this calendar month: weekly runs (state/runs.jsonl) + web sweeps (state/sweeps.jsonl)."""
    m = (today or dt.date.today()).strftime("%Y-%m")
    tot = 0.0
    for r in common.load_jsonl(os.path.join(common.STATE, "runs.jsonl")):
        if str(r.get("finished", ""))[:7] == m:
            tot += float((r.get("claude") or {}).get("cost_usd") or 0)
    for r in common.load_jsonl(os.path.join(common.STATE, "sweeps.jsonl")):
        if str(r.get("date", ""))[:7] == m:
            tot += float(r.get("cost_usd") or 0)
    return round(tot, 4)

def budget_left(budget_usd, run_spent=0.0, today=None):
    return round(float(budget_usd if budget_usd is not None else 1.0) - month_spend(today) - run_spent, 4)

def due_this_month(rows, today=None):
    """True unless a sweep already ran this month: one that succeeded, or that cost money (no paying twice for a failed one).
    Dry runs (testing) never count."""
    m = (today or dt.date.today()).strftime("%Y-%m")
    for r in rows:
        if str(r.get("date", ""))[:7] != m or r.get("dry_run"):
            continue
        if r.get("ok") or r.get("cost_usd") or ("ok" not in r and "proposed" in r):
            return False
    return True
