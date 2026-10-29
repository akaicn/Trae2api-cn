"""Query get_detail_param with different ``function`` values.

The relay pins function=solo_work_lite (the Work tier).  The native binary
lists many functions (solo_agent, solo_agent_lite, ...).  This probe checks
whether other tiers report Max / bigger context_window_tokens for the same
account on the same gateway.
"""

import asyncio
import json
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

import httpx  # noqa: E402

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()

FUNCTIONS = [
    "solo_work_lite",
    "solo_agent",
    "solo_agent_lite",
    "solo_agent_remote",
    "solo_work_remote",
]
WANTED = "glm-5.3-flash"


def _c(name: str, default: str = "") -> str:
    return str(getattr(tc, name, default) or default)


async def fetch(function: str) -> dict:
    token = auth.get_token()
    base = tc._model_gateway_base()
    url = f"{base}/api/ide/v1/get_detail_param"
    body = {
        "function": function,
        "config_names": None,
        "need_prompt": False,
        "current_config_info": None,
        "poly_prompt": True,
        "mode_type": None,
        "agent_type": None,
    }
    origin = tc.web_origin()
    headers = tc.build_headers(token_override=token)
    headers.update(
        {
            "Accept": "application/json",
            "Origin": origin,
            "Referer": origin + "/",
            "x-app-version": "default",
            "x-app-version-code": _c("SOLO_GATEWAY_VERSION_CODE"),
            "x-ide-version": _c("SOLO_GATEWAY_VERSION"),
            "x-ide-version-code": _c("SOLO_GATEWAY_VERSION_CODE"),
            "x-ide-version-type": "stable",
            "x-device-brand": _c("SOLO_DEVICE_BRAND"),
            "x-device-type": _c("SOLO_DEVICE_TYPE"),
            "x-device-platform": _c("SOLO_PLATFORM"),
            "x-version-code": _c("SOLO_GATEWAY_VERSION_CODE"),
            "x-os-version": _c("SOLO_OS_VERSION"),
            "package-type": _c("SOLO_PACKAGE_TYPE"),
            "x-lscbd-aid": _c("SOLO_LSCBD_AID"),
            "x-lscbd-platform": _c("SOLO_PLATFORM"),
            "x-ss-dp": _c("SOLO_LSCBD_AID"),
            "x-plugin-channel": "icube-ai",
            "app-version": _c("SOLO_GATEWAY_VERSION"),
            "request-traffic-type": "prod",
            "User-Agent": f"Trae/{_c('SOLO_GATEWAY_VERSION')}",
        }
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            return {"_error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
    if not isinstance(data, dict):
        return {"_error": "non-dict response"}
    raw = data.get("config_info_list")
    if not isinstance(raw, list):
        nested = data.get("data")
        raw = nested.get("config_info_list") if isinstance(nested, dict) else []
    return {"_raw": raw or [], "_error": None}


async def main() -> None:
    for function in FUNCTIONS:
        result = await fetch(function)
        if result["_error"]:
            print(f"== function={function}: ERROR {result['_error']}")
            continue
        raw_list = result["_raw"]
        hit = None
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("config_name") or raw.get("name") or "")
            if name.lower() == WANTED:
                hit = raw
                break
        if not hit:
            print(f"== function={function}: {len(raw_list)} configs, {WANTED} NOT FOUND")
            continue
        dc = hit.get("display_config") if isinstance(hit.get("display_config"), dict) else {}
        slim = {
            "max_mode": bool(hit.get("max_mode") or dc.get("max_mode")),
            "context_window_tokens": hit.get("context_window_tokens"),
            "context_window_size": hit.get("context_window_size"),
            "features": hit.get("features"),
            "can_use_super_model": dc.get("can_use_super_model"),
            "fee_model_level": dc.get("fee_model_level"),
        }
        print(f"== function={function}: {len(raw_list)} configs")
        print("   " + json.dumps(slim, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
