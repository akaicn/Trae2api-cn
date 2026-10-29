"""Probe: can the personal account use /api/ide/v2/llm_raw_chat (= official
main chat path) and does a top-level reasoning_effort take effect there?

The official client sends main chat through llm_raw_chat with a top-level
``reasoning_effort`` / ``reasoning_effort_level`` field.  The relay only uses
that path for TraeWork's text transport and never sends effort.

This bypasses the relay and posts directly, so we can see the raw upstream
verdict (including the 2001 app-config error the README mentions).
"""

import asyncio
import json
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import raw_client  # noqa: E402

auth.init_auth()

QUESTION = "How many r letters are in the word strawberry? Think briefly."


async def probe(label, effort_key=None, effort_value=None):
    options = {
        "max_tokens": 2048,
    }
    # Force a plain text build (no tools) so we exercise the minimal body.
    try:
        raw_model = await raw_client.resolve_raw_model_for_request("glm-5.3-flash", options)
        body = raw_client.build_raw_chat_body(
            [{"role": "user", "content": QUESTION}], "glm-5.3-flash", options
        )
    except Exception as exc:
        print(f"[{label}] build failed: {type(exc).__name__}: {exc}")
        return

    if effort_value is not None:
        body[effort_key] = effort_value
    print(f"[{label}] body_keys={sorted(body.keys())} config_name={body.get('config_name')} raw_model={raw_model.raw_model_name}")

    token = auth.get_token()
    base_url = raw_client.resolve_raw_base_url(options)
    req_id = "probe-" + label
    headers = raw_client.build_raw_headers(base_url, token, raw_model, req_id, options)
    url = base_url + raw_client.RAW_CHAT_ENDPOINT

    import httpx

    try:
        with httpx.Client(timeout=120) as client:
            with client.stream("POST", url, headers=headers, json=body) as resp:
                status = resp.status_code
                lines = [ln for ln in resp.iter_lines() if ln.strip()]
    except Exception as exc:
        print(f"[{label}] HTTP error: {type(exc).__name__}: {exc}")
        return

    print(f"[{label}] HTTP {status}, {len(lines)} SSE lines")
    usage, err, reasoning_chars, reply = None, None, 0, ""
    for ln in lines:
        if ln.startswith("data:"):
            try:
                p = json.loads(ln[5:])
            except Exception:
                continue
            if isinstance(p, dict):
                if "prompt_tokens" in p:
                    usage = p
                if p.get("code") or p.get("error"):
                    err = p
                if p.get("reasoning_content"):
                    reasoning_chars += len(p["reasoning_content"])
                if p.get("response"):
                    reply += p["response"]
    if err:
        print(f"[{label}] upstream error payload: {json.dumps(err, ensure_ascii=False)[:400]}")
    if usage:
        print(f"[{label}] usage reasoning_tokens={usage.get('reasoning_tokens')} prompt={usage.get('prompt_tokens')}")
    print(f"[{label}] reasoning_chars={reasoning_chars} reply={reply[:120]!r}")


async def main():
    await probe("1-raw-control")
    await probe("2-raw-effort-light", "reasoning_effort", "light")
    await probe("3-raw-effort-extra_high", "reasoning_effort", "extra_high")
    await probe("4-raw-effort-level-extra_high", "reasoning_effort_level", "extra_high")


if __name__ == "__main__":
    asyncio.run(main())
