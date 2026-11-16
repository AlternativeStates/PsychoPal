"""Checks that the Claude API key works, using one tiny request (a fraction of a cent) and a free model lookup for the sweep model.

  python -m psychopal.check_claude           # skipped while Claude is switched off (llm.enabled: false)
  python -m psychopal.check_claude --force   # test the key anyway (e.g. before switching Claude on)
Exit code 0 = OK (or switched off). Prints a plain-English reason when it fails (wrong key, workspace, no credits, model access).
Never prints the key itself, only its shape (prefix family and length)."""
import os, sys
from . import llm
from .common import anthropic_client, load_yaml

def key_shape(raw):
    k = (raw or "").strip()
    fam = next((p for p in ("sk-ant-api", "sk-ant-admin", "sk-ant-oat") if k.startswith(p)), "unrecognised prefix")
    note = ""
    if fam == "sk-ant-admin":
        note = " - this is an ADMIN key, not an API key: create a normal API key in the Console"
    elif fam == "sk-ant-oat":
        note = " - this is an OAuth token, not an API key: create a normal API key in the Console"
    ws = "; surrounding spaces/newlines were stripped" if k != (raw or "") else ""
    return f"key {fam}…, {len(k)} characters{ws}{note}"

def main(argv=None):
    force = "--force" in (argv if argv is not None else sys.argv[1:])
    settings = load_yaml("settings.yaml")
    cfg = settings.get("llm") or {}
    enabled = cfg.get("enabled", True)
    raw = os.environ.get("ANTHROPIC_API_KEY")
    if not raw:
        if not enabled:
            print("OFF: Claude is switched off (llm.enabled: false) and no ANTHROPIC_API_KEY is set; cards use scripted summaries.")
            return 0
        print("FAIL: ANTHROPIC_API_KEY is not set (GitHub: add it under Settings > Secrets and variables > Actions).")
        return 1
    if not enabled and not force:
        print("OFF: Claude is switched off (llm.enabled: false in config/settings.yaml); cards use scripted abstract summaries.")
        return 0
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
        print(f"{pre}OK: {model} answered {text!r} (workspace {ws}; {key_shape(raw)}{extra})")
        return 0
    except anthropic.AuthenticationError as e:
        print(f"{pre}FAIL: the key was rejected (typo, expired, disabled or deleted?). {key_shape(raw)}. {e.message}")
    except anthropic.PermissionDeniedError as e:
        print(f"{pre}FAIL: the key is not allowed to use this model or workspace. {e.message}")
    except anthropic.NotFoundError as e:
        print(f"{pre}FAIL: model or workspace not found ({model} / {(settings.get('sweep') or {}).get('model')}). {e.message}")
    except anthropic.BadRequestError as e:
        m = e.message or ""
        if "workspace" in m.lower():
            print(f"{pre}FAIL: this key is not scoped to a workspace. Either create a new key with Workspace = PsychoPal, or add the "
                  "workspace ID (wrkspc_...) as the secret ANTHROPIC_WORKSPACE_ID.")
        elif "credit balance" in m.lower():
            print(f"{pre}FAIL: the Console organization has no credits. Buy credits at platform.claude.com/settings/billing.")
        else:
            print(f"{pre}FAIL: request rejected: {m}")
    except anthropic.RateLimitError as e:
        print(f"{pre}FAIL: rate limited or workspace spend limit reached. {e.message}")
    except anthropic.APIStatusError as e:
        print(f"{pre}FAIL: API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        print(f"{pre}FAIL: could not reach the Claude API (network).")
    return 1

if __name__ == "__main__":
    sys.exit(main())
