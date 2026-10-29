"""T1: does function=solo_agent still do native OpenAI tool calls?

Sends a tiny request with a real tools schema on the agent tier and inspects
the SSE output/tool_calls events.  Also compares against the stock
solo_work_lite tier as a control.
"""

import asyncio
import json
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"],
            },
        },
    }
]


async def probe(label: str, function: str, use_tools: bool) -> None:
    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = function
        return body

    tc.build_llm_chat_body = patched
    options = {"max_tokens": 256}
    if use_tools:
        options["tools"] = TOOLS
        options["tool_choice"] = "auto"
    try:
        try:
            resp = await tc.send_chat_request(
                [
                    {
                        "role": "user",
                        "content": "What is the weather in Paris right now? Use the tool.",
                    }
                ],
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

    reply, tool_calls, usage = "", [], None
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

    print(f"[{label}] function={function} tools_sent={use_tools}")
    print(f"    reply={reply[:200]!r}")
    print(f"    tool_calls={json.dumps(tool_calls, ensure_ascii=False)[:600]}")
    if usage:
        print(f"    prompt_tokens={usage.get('prompt_tokens')} cluster={usage.get('cluster')}")


async def main() -> None:
    await probe("1-work_lite+tools (stock control)", "solo_work_lite", True)
    await probe("2-solo_agent+tools (candidate)", "solo_agent", True)


if __name__ == "__main__":
    asyncio.run(main())
