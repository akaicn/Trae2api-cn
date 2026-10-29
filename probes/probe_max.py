"""Probe: does the llm_utils_chat gateway read context_window_size?

Runs three tiny chat probes against the IDE gateway with the Max field set
injected, varying context_window_size to see whether upstream validates it.
Also dumps the raw SSE lines so we can diff baseline vs max payloads.

Usage:  .venv\\Scripts\\python probe_max.py   (cwd = this repo root)
"""

import asyncio
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()  # same entry point the relay runs at startup


async def probe(label: str, ctx_value, max_fields: bool = True) -> None:
    orig = tc.build_llm_chat_body

    def patched(messages, model, stream, max_tokens=None, options=None):
        body = orig(messages, model, stream, max_tokens, options)
        # orig() already injects the Max family when options has trae_max_mode.
        # Overwrite context_window_size for the validation probes.
        if ctx_value is not None:
            body["context_window_size"] = ctx_value
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
    options = {"trae_max_mode": True, "max_tokens": 64}
    try:
        try:
            resp = await tc.send_chat_request(
                [{"role": "user", "content": "Reply with exactly: pong"}],
                "glm-5.3-flash",
                False,
                options,
            )
        except Exception as exc:  # upstream 4xx/5xx surfaces here
            print(f"[{label}] REQUEST ERROR: {exc}")
            return
        try:
            lines = [line for line in resp.response.iter_lines() if line.strip()]
        finally:
            resp.close()
    finally:
        tc.build_llm_chat_body = orig

    print(f"[{label}] OK  sse_lines={len(lines)}")
    for line in lines[:25]:
        print("    " + line[:400])
    if len(lines) > 25:
        print(f"    ... ({len(lines) - 25} more)")


async def main() -> None:
    # A: control - no Max fields at all (stock relay behaviour)
    await probe("A-control-no-max", None, max_fields=False)
    # B: valid 1M
    await probe("B-max-1000000", 1_000_000)
    # C: absurd small - if upstream reads the field this should fail/clamp
    await probe("C-max-ctx1", 1)
    # D: absurd large
    await probe("D-max-ctx999999999", 999_999_999)


if __name__ == "__main__":
    asyncio.run(main())
