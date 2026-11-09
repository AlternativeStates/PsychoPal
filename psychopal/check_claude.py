"""Checks that the Claude API key works, using one tiny request (costs a fraction of a cent).

  python -m psychopal.check_claude
Exit code 0 = OK. Prints a plain-English reason when it fails (wrong key, workspace missing, no credits, model not enabled)."""
import os, sys
from .common import anthropic_client, load_yaml

def main():
    if not (load_yaml("settings.yaml").get("llm") or {}).get("enabled", True):
        print("OFF: Claude is switched off (llm.enabled: false in config/settings.yaml); cards use scripted abstract summaries.")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("FAIL: ANTHROPIC_API_KEY is not set (GitHub: add it under Settings > Secrets and variables > Actions).")
        return 1
    import anthropic
    model = (load_yaml("settings.yaml").get("llm") or {}).get("model", "claude-sonnet-5-5")
    try:
        raw = anthropic_client().messages.with_raw_response.create(
            model=model, max_tokens=1024, output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        msg = raw.parse()
        text = next((b.text for b in msg.content if b.type == "text"), "").strip()
        ws = raw.headers.get("anthropic-workspace-id") or "?"
        print(f"OK: {model} answered {text!r} (workspace {ws}; {msg.usage.input_tokens} in / {msg.usage.output_tokens} out tokens)")
        return 0
    except anthropic.AuthenticationError as e:
        print(f"FAIL: the key was rejected (typo, expired, disabled or deleted?). {e.message}")
    except anthropic.PermissionDeniedError as e:
        print(f"FAIL: the key is not allowed to use this model or workspace. {e.message}")
    except anthropic.NotFoundError as e:
        print(f"FAIL: model or workspace not found ({model}). {e.message}")
    except anthropic.BadRequestError as e:
        m = e.message or ""
        if "workspace" in m.lower():
            print("FAIL: this key is not scoped to a workspace. Either create a new key with Workspace = PsychoPal, or add the "
                  "workspace ID (wrkspc_...) as the secret ANTHROPIC_WORKSPACE_ID.")
        elif "credit balance" in m.lower():
            print("FAIL: the Console organization has no credits. Claim your Team API credits or buy credits at "
                  "platform.claude.com/settings/billing.")
        else:
            print(f"FAIL: request rejected: {m}")
    except anthropic.RateLimitError as e:
        print(f"FAIL: rate limited or workspace spend limit reached. {e.message}")
    except anthropic.APIStatusError as e:
        print(f"FAIL: API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        print("FAIL: could not reach the Claude API (network).")
    return 1

if __name__ == "__main__":
    sys.exit(main())
