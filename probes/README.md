# 探针脚本

本目录是 `端点能力实测报告.md` 的证据脚本，全部在**测试副本**（独立目录、独立端口）中运行，
**不会修改正在运行的正式实例**。

## 用法

使用正式实例的 venv Python，从本目录运行即可（脚本内部已指定 `sys.path` 与 `auth.init_auth()`）：

```powershell
& "D:\Apps\trae2api-cn\.venv\Scripts\python.exe" .\probe_limit_map.py solo_agent max_on 400000
```

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `probe_solo_catalog.py` | 查 `get_detail_param` 模型目录（context_window / max_mode） |
| `probe_functions.py` | 遍历不同 `function` 档位查目录差异（work_lite vs agent 档） |
| `probe_max.py` | 旧的 Max 字段可见性探针（ctx=1 / 999999999） |
| `probe_agent_tier.py` | `solo_agent` 档小请求工具/文本对照 |
| `probe_tool_agent.py` | 档位切换后的原生工具调用对照 |
| `probe_bigctx.py` | 234K token 大上下文 + Max 字段（哨兵验证） |
| `probe_bigctx_control.py` | 同上但**不带** Max 字段（对照） |
| `probe_bigctx_worklite.py` | 同上但用 `solo_work_lite` 档（对照） |
| `probe_limit_map.py` | **上下文上限二分**（参数：function / max_on|max_off / target_tokens） |
| `probe_1m_limit.py` | ~1.05M token 1M 上限探针 |
| `probe_1m_debug.py` | 1M 上限请求的原始 SSE dump |
| `probe_combo.py` | **工具 + ~900K 上下文同一请求** |
| `probe_effort_inject.py` | 往 `llm_utils_chat` 注入 `custom_model.reasoning_effort` 的无效性验证 |
| `probe_raw_effort.py` | 直打官方 `/api/ide/v2/llm_raw_chat`（验证 2001 app config 错误） |
| `probe_full_sse.py` | 完整 SSE 事件 dump（找 Max/强度回执） |
| `probe_relay_e2e.py` | **端到端**：走 relay `/v1/chat/completions?endpoint=ide` 发大上下文 + 工具 |

> 注意：脚本会真实消耗积分（大上下文探针每个约数积分）。运行前请确认余额与账号。
