"""T2: is there a real 1M hard limit on llm_utils_chat?

Sends ~1.05M tokens (just above the catalog max=1_000_000) through the
solo_agent tier WITH Max fields, markers at head and tail.

Outcome A (error / capped): a real 1M enforcement exists up there.
Outcome B (full 1.05M processed, markers echoed): 1M is also just a label.
"""

import asyncio
import json
import random
import sys
import time

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()

WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "omicron pi rho sigma tau upsilon phi chi psi omega north south east west "
    "river mountain valley forest ocean desert canyon meadow harbor summit"
).split()


def build_content(target_words: int) -> str:
    rng = random.Random(20261012)
    head = "NONCE=8347"
    tail = "TAIL-OK-7391"
    parts = [head, "\nFiller follows; obey only the final instruction."]
    parts.append("---FILLER---")
    words = []
    for i in range(target_words):
        words.append(rng.choice(WORDS))
        if i % 97 == 0:
            words.append(str(i))
    parts.append(" ".join(words))
    parts.append("---END-FILLER---")
    parts.append(f"[FINAL-INSTRUCTION] Reply with exactly: {head} {tail}")
    return "\n".join(parts)


async def main() -> None:
    # ~1.17 tokens/word measured earlier -> need ~900K words for ~1.05M tokens
    content = build_content(900_000)
    print(f"payload chars: {len(content):,} (est. ~1.05M tokens)")

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"
        return body

    tc.build_llm_chat_body = patched
    started = time.time()
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": content}],
                "glm-5.3-flash",
                False,
                {"trae_max_mode": True, "max_tokens": 256},
            )
        except Exception as exc:
            print(f"REQUEST ERROR after {time.time()-started:.0f}s: {exc}")
            return
        try:
            lines = [line for line in resp.response.iter_lines() if line.strip()]
        finally:
            resp.close()
    finally:
        tc.build_llm_chat_body = orig
    print(f"stream finished in {time.time()-started:.0f}s, {len(lines)} lines")

    reply, usage = "", None
    notices = []
    for line in lines:
        if line.startswith("data:"):
            data = line[5:]
            try:
                payload = json.loads(data)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("response"):
                reply += payload["response"]
            if "prompt_tokens" in payload:
                usage = payload
            if "progress" in data.lower() or "notice" in data.lower():
                notices.append(data[:300])

    if usage:
        print(
            f"prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} "
            f"cluster={usage.get('cluster')}"
        )
    for n in notices[:5]:
        print(f"    notice: {n}")
    print(f"reply={reply[:200]!r}")
    head_ok = "NONCE=8347" in reply
    tail_ok = "TAIL-OK-7391" in reply
    tokens = usage.get("prompt_tokens") if usage else None
    print(f"VERDICT head_nonce={head_ok} tail_marker={tail_ok} prompt_tokens={tokens}")
    if head_ok and tail_ok and tokens and tokens > 1_000_000:
        print("=> NO 1M ENFORCEMENT: full >1M input processed")
    elif tokens and tokens < 1_000_000:
        print("=> CAPPED/TRUNCATED below 1M")
    else:
        print("=> INCONCLUSIVE (see error above)")


if __name__ == "__main__":
    asyncio.run(main())
