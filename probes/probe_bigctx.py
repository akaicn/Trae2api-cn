"""DEFINITIVE probe: does Max actually widen the context on llm_utils_chat?

Catalog facts (same gateway, same account):
  function=solo_work_lite -> max_mode=false, context dev=200000
  function=solo_agent     -> max_mode=true,  context dev=200000 + max=1000000

This sends ONE request of roughly 260K tokens (well above the 200K dev
window) with function=solo_agent + injected Max fields.

Success = reply echoes both head nonce and tail instruction AND
          usage.prompt_tokens > 200000
        -> input fully processed beyond the dev window -> Max is REAL.

Failure = upstream error, prompt_tokens capped at ~200000, or the model
          cannot echo the markers (truncation) -> Max fields not honored.
"""

import asyncio
import random
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()

WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "omicron pi rho sigma tau upsilon phi chi psi omega north south east west "
    "river mountain valley forest ocean desert canyon meadow harbor summit"
).split()


def build_content(target_words: int) -> tuple[str, str]:
    rng = random.Random(20261012)
    head = "NONCE=8347"
    tail = "TAIL-OK-7391"
    parts = [head, "\nYou will see filler text followed by a final instruction line."]
    parts.append("Ignore the filler; obey only the final instruction.\n---FILLER---")
    words = []
    for i in range(target_words):
        words.append(rng.choice(WORDS))
        if i % 97 == 0:
            words.append(str(i))
    parts.append(" ".join(words))
    parts.append("---END-FILLER---")
    parts.append(f"[FINAL-INSTRUCTION] Reply with exactly: {head} {tail}")
    return "\n".join(parts), f"{head} {tail}"


async def main() -> None:
    content, expected = build_content(200_000)  # ~250-270K tokens
    print(f"payload chars: {len(content):,}")

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"
        return body

    tc.build_llm_chat_body = patched
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": content}],
                "glm-5.3-flash",
                False,
                {"trae_max_mode": True, "max_tokens": 256},
            )
        except Exception as exc:
            print(f"REQUEST ERROR: {exc}")
            return
        try:
            lines = [line for line in resp.response.iter_lines() if line.strip()]
        finally:
            resp.close()
    finally:
        tc.build_llm_chat_body = orig

    import json

    reply, usage, events = "", None, []
    for line in lines:
        if line.startswith("event:"):
            events.append(line.split(":", 1)[1].strip())
        if line.startswith("data:"):
            data = line[5:]
            if '"prompt_tokens"' in data:
                try:
                    payload = json.loads(data)
                    if isinstance(payload, dict) and "prompt_tokens" in payload:
                        usage = payload
                except Exception:
                    pass
            if '"response":' in data:
                try:
                    payload = json.loads(data)
                    if isinstance(payload, dict) and payload.get("response"):
                        reply += payload["response"]
                except Exception:
                    pass

    print(f"events: {events}")
    if usage:
        print(
            f"prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"cluster={usage.get('cluster')}"
        )
    print(f"reply={reply!r}")
    head_ok = "NONCE=8347" in reply
    tail_ok = "TAIL-OK-7391" in reply
    big_ok = bool(usage and (usage.get("prompt_tokens") or 0) > 200_000)
    print(f"VERDICT head_nonce={head_ok} tail_marker={tail_ok} prompt>200K={big_ok}")
    print("=> MAX HONORED" if (head_ok and tail_ok and big_ok) else "=> NOT PROVEN")


if __name__ == "__main__":
    asyncio.run(main())
