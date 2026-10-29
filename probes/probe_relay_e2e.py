"""T4: end-to-end through the relay's OpenAI-compatible endpoint.

POST /v1/chat/completions to the test instance (127.0.0.1:8010) with a large
filler history (~300K chars) plus a real tools schema, forcing the IDE Agent
endpoint, and check that the returned message contains a native tool_call.

This validates the whole chain (auth, mode dispatch, message conversion,
SSE relay) rather than the raw upstream probe.
"""

import json
import random
import sys

import httpx

BASE = "http://127.0.0.1:8010"
API_KEY = "sk-IziutBl9P63UCrxYIiORvqLtWFcJgXzG1l72EI5kTNl6vKwm"

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


def filler(words: int) -> str:
    rng = random.Random(7)
    return " ".join(rng.choice(WORDS) for _ in range(words))


def main() -> None:
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Context dump (ignore): " + filler(220_000)},
        {"role": "assistant", "content": "Understood, context noted."},
        {
            "role": "user",
            "content": "Call the get_weather tool for Paris. Do not answer in text.",
        },
    ]
    total_chars = sum(len(m.get("content", "")) for m in messages)
    print(f"messages={len(messages)} total_chars={total_chars:,} (~{total_chars//4:,} tokens)")

    payload = {
        "model": "glm-5.3-flash",
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    # Force the IDE endpoint via the documented query parameter
    resp = httpx.post(
        f"{BASE}/v1/chat/completions",
        params={"endpoint": "ide"},
        headers=headers,
        json=payload,
        timeout=600,
    )
    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print(resp.text[:1000])
        return
    choices = data.get("choices") or []
    if not choices:
        print(json.dumps(data, ensure_ascii=False)[:1200])
        return
    msg = choices[0].get("message") or {}
    print(f"finish_reason={choices[0].get('finish_reason')}")
    print(f"content={(msg.get('content') or '')[:200]!r}")
    print(f"tool_calls={json.dumps(msg.get('tool_calls'), ensure_ascii=False)[:600]}")
    print(f"usage={json.dumps(data.get('usage'), ensure_ascii=False)}")
    tool_ok = bool(msg.get("tool_calls"))
    print(f"VERDICT relay_e2e_tool_call={tool_ok}")


if __name__ == "__main__":
    main()
