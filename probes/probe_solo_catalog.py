"""Dump the Solo/IDE gateway model catalog (get_detail_param).

Shows what context_window / max_mode dimensions the llm_utils_chat channel
itself declares for the models we care about.
"""

import asyncio
import json
import sys

sys.path.insert(0, r"D:\Apps\trae2api-cn-test")

from src import auth  # noqa: E402
from src import trae_client as tc  # noqa: E402

auth.init_auth()


async def main() -> None:
    cfgs = await tc._fetch_solo_model_configs()
    print(f"catalog size: {len(cfgs)}")
    if not cfgs:
        print("EMPTY catalog (token issue or upstream error)")
        return
    wanted = ["glm-5.3-flash", "glm-5.3", "mimo-v2.6-flash"]
    lower = {str(k).lower(): k for k in cfgs}
    for name in wanted:
        key = lower.get(name.lower())
        if not key:
            print(f"== {name}: NOT in catalog; sample keys: {list(cfgs)[:15]}")
            continue
        cfg = cfgs[key]
        slim = {
            k: cfg.get(k)
            for k in (
                "config_name",
                "model_name",
                "max_mode",
                "context_window_size",
                "context_window_tokens",
                "features",
                "display_config",
                "prompt_max_tokens",
                "max_tokens",
            )
            if k in cfg
        }
        print(f"== {key}")
        print(json.dumps(slim, ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    asyncio.run(main())
