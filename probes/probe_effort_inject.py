"""Probe: will llm_utils_chat honor custom_model.reasoning_effort?

The official client sends thinking effort inside custom_model via the
chat.createSession path.  The relay's IDE endpoint uses llm_utils_chat and
never sends custom_model.  This probe manually injects the official-style
custom_model (with reasoning_effort / reasoning_effort_level) and compares
against a no-effort control, dumping the raw SSE for any effort evidence.

Run: python probe_effort_inject.py
"""

import asyncio
import json
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()

QUESTION = "How many r letters are in the word strawberry? Think step by step."


async def probe(label, mode, effort_value=None, effort_key="reasoning_effort"):
    orig = tc.build_llm_chat_body
    custom_model = {}
    if effort_value is not None:
        # Fetch the official-style custom_model object for the model
        try:
            cm = await tc._get_web_custom_model("glm-5.3-flash")
            if cm:
                custom_model = dict(cm)
            else:
                custom_model = {"config_name": "glm-5.3-flash"}
        except Exception as exc:
            print(f"[{label}] custom_model fetch failed: {exc}")
            custom_model = {"config_name": "glm-5.3-flash"}
        custom_model[effort_key] = effort_value

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"  # agent tier
        if effort_value is not None and custom_model:
            body["custom_model"] = custom_model
        return body

    tc.build_llm_chat_body = patched
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": QUESTION}],
                "glm-5.3-flash",
                False,
                {"max_tokens": 4096},
            )
        except Exception as exc:
            print(f"[{label}] REQUEST ERROR: {exc}")
            return
        try:
            lines = [line for line in resp.response.iter_lines() if line.strip()]
        finally:
            resp.close()
    finally:
        tc.build_llm_chat_body = orig

    usage, err, reply_len = None, None, 0
    for line in lines:
        if line.startswith("data:"):
            try:
                p = json.loads(line[5:])
            except Exception:
                continue
            if isinstance(p, dict):
                if "prompt_tokens" in p:
                    usage = p
                if p.get("code"):
                    err = p
                if p.get("reasoning_content"):
                    reply_len += len(p["reasoning_content"])
    if err:
        print(f"[{label}] upstream error: {err}")
        return
    rt = usage.get("reasoning_tokens") if usage else None
    print(
        f"[{label}] mode={mode} effort_key={effort_key if effort_value else '-'} "
        f"value={effort_value} -> reasoning_tokens={rt} reasoning_chars={reply_len} "
        f"prompt={usage.get('prompt_tokens') if usage else '?'}"
    )


async def main():
    await probe("A-control-none", "ide", None)
    await probe("B-effort-level-extra_high", "ide", "extra_high", "reasoning_effort_level")
    await probe("C-effort-extra_high", "ide", "extra_high", "reasoning_effort")
    await probe("D-effort-level-light", "ide", "light", "reasoning_effort_level")


if __name__ == "__main__":
    asyncio.run(main())
