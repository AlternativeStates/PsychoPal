"""Checks that the Claude API key works, using one tiny request (a fraction of a cent) and a free model lookup for the sweep model.

  python -m psychopal.check_claude           # skipped while Claude is switched off (llm.enabled: false)
  python -m psychopal.check_claude --force   # test the key anyway (e.g. before switching Claude on)
  python -m psychopal.check_claude --force --save state/claude_check.json   # also record the verdict (CI does this)
Exit code 0 = OK (or switched off). Prints a plain-English reason when it fails (wrong key, workspace, no credits, model access).
Never prints the key itself, only its shape (prefix family and length). The saved file goes into the public repo, so it holds
only the verdict: no key shape, no workspace ID."""
import datetime as dt, json, os, sys
from . import llm
from .common import anthropic_client, load_yaml

def key_shape(raw):
    k = (raw or "").strip()
    fam = next((p for p in ("sk-ant-api", "sk-ant-admin", "sk-ant-oat") if k.startswith(p)), "with another prefix")
    note = ""
    if fam == "sk-ant-admin":
        note = " - this is an ADMIN key, not an API key: create a normal API key in the Console"
    elif fam == "sk-ant-oat":
        note = " - this is an OAuth token, not an API key: create a normal API key in the Console"
    ws = "; surrounding spaces/newlines were stripped" if k != (raw or "") else ""
    return f"key {fam}…, {len(k)} characters{ws}{note}"

def check(force=False):
    """Returns (status, printed message, public message); status is "ok", "off" or "fail"."""
    settings = load_yaml("settings.yaml")
    cfg = settings.get("llm") or {}
    enabled = cfg.get("enabled", True)
    raw = os.environ.get("ANTHROPIC_API_KEY")
    if not raw:
        if not enabled:
            m = "OFF: Claude is switched off (llm.enabled: false) and no ANTHROPIC_API_KEY is set; cards use scripted summaries."
            return "off", m, m
        m = "FAIL: no Claude API key reached the run (GitHub: add it as the secret PSYCHOPAL_2027 under Settings > Secrets and variables > Actions)."
        return "fail", m, m
    if not enabled and not force:
        m = "OFF: Claude is switched off (llm.enabled: false in config/settings.yaml); cards use scripted abstract summaries."
        return "off", m, m
    pre = "" if enabled else "(Claude is switched off; key tested anyway) "
    import anthropic
    model, effort = cfg.get("model", "claude-haiku-5-5"), "low"
    try:
        params = {"model": model, "max_tokens": 256, "output_config": {"effort": effort},
                  "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}
        th = llm.thinking_param(model, "off", effort)
        if th:
            params["thinking"] = th
        client = anthropic_client()
        raw_resp = client.messages.with_raw_response.create(**params)
        msg = raw_resp.parse()
        text = next((b.text for b in msg.content if b.type == "text"), "").strip()
        ws = raw_resp.headers.get("anthropic-workspace-id") or "?"
        extra = ""
        sweep = settings.get("sweep") or {}
        if sweep.get("enabled", True) and sweep.get("model") and sweep["model"] != model:
            client.models.retrieve(sweep["model"])        # free: confirms the key may use the sweep model too
            extra = f"; {sweep['model']} available for the monthly sweep"
        return ("ok", f"{pre}OK: {model} answered {text!r} (workspace {ws}; {key_shape(raw)}{extra})",
                f"{pre}OK: {model} answered {text[:20]!r}{extra}")
    except anthropic.AuthenticationError as e:
        m = f"{pre}FAIL: the key was rejected (typo, expired, disabled or deleted?)."
        return "fail", f"{m} {key_shape(raw)}. {e.message}", m
    except anthropic.PermissionDeniedError as e:
        m = f"{pre}FAIL: the key is not allowed to use this model or workspace. {e.message}"
    except anthropic.NotFoundError as e:
        m = f"{pre}FAIL: model or workspace not found ({model} / {(settings.get('sweep') or {}).get('model')}). {e.message}"
    except anthropic.BadRequestError as e:
        t = e.message or ""
        if "workspace" in t.lower():
            m = (f"{pre}FAIL: this key is not scoped to a workspace. Either create a new key with Workspace = PsychoPal, or add the "
                 "workspace ID (wrkspc_...) as the secret ANTHROPIC_WORKSPACE_ID.")
        elif "credit balance" in t.lower():
            m = f"{pre}FAIL: the Console organization has no credits. Buy credits at platform.claude.com/settings/billing."
        else:
            m = f"{pre}FAIL: request rejected: {t}"
    except anthropic.RateLimitError as e:
        m = f"{pre}FAIL: rate limited or workspace spend limit reached. {e.message}"
    except anthropic.APIStatusError as e:
        m = f"{pre}FAIL: API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        m = f"{pre}FAIL: could not reach the Claude API (network)."
    return "fail", m, m[:300]

def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    status, printed, public = check(force="--force" in args)
    print(printed)
    if "--save" in args and args.index("--save") + 1 < len(args):
        with open(args[args.index("--save") + 1], "w", encoding="utf-8") as f:
            json.dump({"date": dt.date.today().isoformat(), "status": status, "result": public}, f, ensure_ascii=False, indent=1)
            f.write("\n")
    return 1 if status == "fail" else 0

if __name__ == "__main__":
    sys.exit(main())
