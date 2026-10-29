"""CONTROL probe (B2): same ~260K-token request but on function=solo_work_lite
(the Work tier the relay pins; catalog declares max_mode=false, dev=200000),
WITHOUT Max fields.

If tiers are enforced server-side, this one should fail while the
solo_agent runs succeed.
"""

import asyncio
import json
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
    rng = random.Random(20261012)  # identical payload to probes A / B1
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
    content, _expected = build_content(200_000)
    print(f"payload chars: {len(content):,}")

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_work_lite"  # Work tier, stock relay behaviour
        for key in (
            "model_selection_strategy",
            "model_auto_selection",
            "context_window_size",
            "mode_type",
        ):
            body.pop(key, None)
        return body

    tc.build_llm_chat_body = patched
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": content}],
                "glm-5.3-flash",
                False,
                {"max_tokens": 256},  # no trae_max_mode
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

    reply, usage = "", None
    for line in lines:
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
    print(
        "=> work tier also passes at 234K (no tier enforcement either)"
        if (head_ok and tail_ok and big_ok)
        else "=> work tier FAILS at 234K -> tier/window enforced server-side"
    )


if __name__ == "__main__":
    asyncio.run(main())
