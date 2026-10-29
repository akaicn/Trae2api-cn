"""T2-debug: dump the raw SSE of the ~1.05M-token request verbatim."""

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


def build_content(target_words: int) -> str:
    rng = random.Random(20261012)
    head, tail = "NONCE=8347", "TAIL-OK-7391"
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
    content = build_content(900_000)
    print(f"payload chars: {len(content):,}")

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"
        return body

    tc.build_llm_chat_body = patched
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
    finally:
        tc.build_llm_chat_body = orig
    try:
        lines = [line for line in resp.response.iter_lines() if line.strip()]
    finally:
        resp.close()
    print(f"--- raw SSE ({len(lines)} lines) ---")
    for line in lines:
        print(line[:2000])


if __name__ == "__main__":
    asyncio.run(main())
