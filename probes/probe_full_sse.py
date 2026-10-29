"""Dump the FULL raw SSE for a solo_agent + Max chat, to look for any
upstream acknowledgement of max/context state in extra_info/done events."""

import asyncio
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()


async def main() -> None:
    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        body["function"] = "solo_agent"
        return body

    tc.build_llm_chat_body = patched
    try:
        resp = await tc.send_chat_request(
            [{"role": "user", "content": "Reply with exactly: pong"}],
            "glm-5.3-flash",
            False,
            {"trae_max_mode": True, "max_tokens": 64},
        )
    finally:
        tc.build_llm_chat_body = orig
    try:
        lines = [line for line in resp.response.iter_lines() if line.strip()]
    finally:
        resp.close()
    for line in lines:
        print(line[:1200])


if __name__ == "__main__":
    asyncio.run(main())
