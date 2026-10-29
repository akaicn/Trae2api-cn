"""T3: full native tool calling AND ~900K context in the SAME request.

function=solo_agent, real tools schema, ~900K-token filler, and a final
instruction to call the tool.  If the model returns a proper tool_call while
prompt_tokens ~= 900K, then "full tools + 1M context" is achievable today.
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

TOKENS_PER_WORD = 234_489 / 200_000

WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "omicron pi rho sigma tau upsilon phi chi psi omega north south east west "
    "river mountain valley forest ocean desert canyon meadow harbor summit"
).split()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def build_content(target_tokens: int) -> str:
    rng = random.Random(20261012)
    n_words = int(target_tokens / TOKENS_PER_WORD)
    parts = ["---FILLER (ignore)---"]
    words = []
    for i in range(n_words):
        words.append(rng.choice(WORDS))
        if i % 97 == 0:
            words.append(str(i))
    parts.append(" ".join(words))
    parts.append("---END-FILLER---")
    parts.append(
        "[FINAL-INSTRUCTION] Call the get_weather tool with city=\"Paris\". "
        "Do not answer in text."
    )
    return "\n".join(parts)


async def main() -> None:
    content = build_content(900_000)
    print(f"payload chars: {len(content):,} (est. ~900K tokens)")

    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"
        return body

    tc.build_llm_chat_body = patched
    started = time.time()
    try:
        resp = await tc.send_chat_request(
            [{"role": "user", "content": content}],
            "glm-5.3-flash",
            False,
            {"trae_max_mode": True, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 256},
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

    reply, tool_calls, usage, error = "", [], None, None
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
            if payload.get("tool_calls"):
                tool_calls.append(payload["tool_calls"])
            if "prompt_tokens" in payload:
                usage = payload
            if payload.get("code"):
                error = payload

    print(f"elapsed {time.time()-started:.0f}s")
    if error:
        print(f"REJECTED code={error.get('code')} msg={error.get('message')!r}")
        return
    if usage:
        print(f"prompt_tokens={usage.get('prompt_tokens'):,} cluster={usage.get('cluster')}")
    print(f"reply={reply[:200]!r}")
    print(f"tool_calls={json.dumps(tool_calls, ensure_ascii=False)[:600]}")
    got_tool = any(
        "get_weather" in json.dumps(tc_chunk) for tc_chunk in tool_calls
    )
    big = bool(usage and (usage.get("prompt_tokens") or 0) > 800_000)
    print(f"VERDICT native_tool_called={got_tool} context>800K={big}")
    print(
        "=> TOOLS + ~1M CONTEXT TOGETHER: YES"
        if (got_tool and big)
        else "=> combination not confirmed"
    )


if __name__ == "__main__":
    asyncio.run(main())
