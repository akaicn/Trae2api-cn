"""Probe: chat on the IDE gateway with an agent-tier ``function``.

The relay pins function=solo_work_lite (Work tier, max_mode=false, dev 200K).
This probe switches the chat body to solo_agent (agent tier, max_mode=true,
dev 200K + max 1M) with and without the injected Max fields, using tiny
prompts.
"""

import asyncio
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()


async def probe(label: str, function: str, max_fields: bool) -> None:
    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = function
        if not max_fields:
            for key in (
                "model_selection_strategy",
                "model_auto_selection",
                "context_window_size",
                "mode_type",
            ):
                body.pop(key, None)
        return body

    tc.build_llm_chat_body = patched
    options = {"max_tokens": 64}
    if max_fields:
        options["trae_max_mode"] = True
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": "Reply with exactly: pong"}],
                "glm-5.3-flash",
                False,
                options,
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

    # Extract reply text and usage from the SSE stream
    reply_parts, usage_line, events = [], None, set()
    for line in lines:
        if line.startswith("event:"):
            events.add(line.split(":", 1)[1].strip())
        if line.startswith("data:"):
            data = line[5:]
            if '"usage"' in data or "prompt_tokens" in data:
                usage_line = data[:400]
            if '"response":' in data:
                try:
                    import json

                    payload = json.loads(data)
                    if isinstance(payload, dict) and payload.get("response"):
                        reply_parts.append(payload["response"])
                except Exception:
                    pass
    print(f"[{label}] OK  events={sorted(events)}")
    print(f"    reply={''.join(reply_parts)!r}")
    if usage_line:
        print(f"    usage_raw={usage_line}")


async def main() -> None:
    await probe("1-work_lite-no-max (stock)", "solo_work_lite", False)
    await probe("2-solo_agent-no-max", "solo_agent", False)
    await probe("3-solo_agent+max-fields", "solo_agent", True)


if __name__ == "__main__":
    asyncio.run(main())
