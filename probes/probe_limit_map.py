"""T2 map: locate the real prompt-token cap on llm_utils_chat.

Runs a small matrix varying (function, max-fields, payload size) to learn
whether the cap is tier-fixed or Max-controlled.

Usage: python probe_limit_map.py <function> <max_on|max_off> <target_tokens>
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

TOKENS_PER_WORD = 234_489 / 200_000  # measured from earlier probes

WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "omicron pi rho sigma tau upsilon phi chi psi omega north south east west "
    "river mountain valley forest ocean desert canyon meadow harbor summit"
).split()


def build_content(target_tokens: int) -> str:
    rng = random.Random(20261012)
    n_words = int(target_tokens / TOKENS_PER_WORD)
    head, tail = "NONCE=8347", "TAIL-OK-7391"
    parts = [head, "\nFiller follows; obey only the final instruction."]
    parts.append("---FILLER---")
    words = []
    for i in range(n_words):
        words.append(rng.choice(WORDS))
        if i % 97 == 0:
            words.append(str(i))
    parts.append(" ".join(words))
    parts.append("---END-FILLER---")
    parts.append(f"[FINAL-INSTRUCTION] Reply with exactly: {head} {tail}")
    return "\n".join(parts)


async def run(function: str, max_on: bool, target_tokens: int) -> None:
    content = build_content(target_tokens)
    print(
        f"== function={function} max_fields={max_on} target~{target_tokens:,} "
        f"tokens (chars={len(content):,})"
    )

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = function
        if not max_on:
            for key in (
                "model_selection_strategy",
                "model_auto_selection",
                "context_window_size",
                "mode_type",
            ):
                body.pop(key, None)
        return body

    tc.build_llm_chat_body = patched
    options = {"max_tokens": 128}
    if max_on:
        options["trae_max_mode"] = True
    started = time.time()
    try:
        resp = await tc.send_chat_request(
            [{"role": "user", "content": content}],
            "glm-5.3-flash",
            False,
            options,
        )
    except Exception as exc:
        tc.build_llm_chat_body = orig
        print(f"   REQUEST ERROR: {exc}")
        return
    finally:
        tc.build_llm_chat_body = orig
    try:
        lines = [line for line in resp.response.iter_lines() if line.strip()]
    finally:
        resp.close()

    reply, usage, error = "", None, None
    for line in lines:
        if line.startswith("data:"):
            data = line[5:]
            try:
                payload = json.loads(data)
            except Exception:
                continue
            if isinstance(payload, dict):
                if payload.get("response"):
                    reply += payload["response"]
                if "prompt_tokens" in payload:
                    usage = payload
                if payload.get("code"):
                    error = payload
    elapsed = time.time() - started
    if error:
        print(f"   REJECTED code={error.get('code')} msg={error.get('message')!r} ({elapsed:.0f}s)")
    elif usage:
        print(
            f"   ACCEPTED prompt_tokens={usage.get('prompt_tokens'):,} "
            f"cluster={usage.get('cluster')} reply_ok={'TAIL-OK-7391' in reply} ({elapsed:.0f}s)"
        )
    else:
        print(f"   UNKNOWN: {len(lines)} lines, reply={reply[:120]!r} ({elapsed:.0f}s)")


async def main() -> None:
    if len(sys.argv) == 4:
        await run(sys.argv[1], sys.argv[2].lower() == "max_on", int(sys.argv[3]))
        return
    # Default matrix
    await run("solo_agent", False, 400_000)
    await run("solo_agent", True, 400_000)
    await run("solo_work_lite", False, 400_000)


if __name__ == "__main__":
    asyncio.run(main())
