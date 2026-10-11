# Windows 托盘守护指南

本文件介绍 [akaicn/Trae2api-cn](https://github.com/akaicn/Trae2api-cn) 在
上游基础上新增的 **Windows 托盘守护机制**。

> 如果你使用 Docker / Linux 服务器部署，请忽略本文件，直接按主
> [README](../README.md) 的 Docker 章节操作。托盘是 **纯 Windows 本机** 的
> 替代方案，二者选其一即可。

---

## 1. 这是什么

用一个小型的系统托盘程序（`tray_app.py`）在后台托管 relay 服务：

- 双击即启动，**不弹黑色命令行窗口**；
- 打开网页控制台、启停授权助手、重启服务、查看日志、退出，全在托盘右键菜单；
- 启动时自动拉起 relay 并做就绪探测；
- 运行期做健康检查，**异常时自动恢复**。

它本身**不修改** relay 的任何业务逻辑，只是"看护"进程：负责拉起、监控、
必要时清理重启。relay 仍然是上游那个 `uvicorn src.main:app`。

---

## 2. 文件清单

| 文件 | 作用 |
|---|---|
| `tray_app.py` | 托盘守护主程序（菜单、进程管理、健康检查、自愈） |
| `启动服务.bat` | 双击入口，转发给 `.vbs` 后立即退出（避免残留 cmd 窗口） |
| `启动服务.vbs` | 用 `pythonw.exe` 无窗口拉起 `tray_app.py` |
| `启动服务-调试.bat` | 调试入口，**保留**一个带实时日志的命令行窗口 |
| `assets/trae2api-cn.ico` / `.png` | 运行中（绿色）托盘图标 |
| `assets/trae2api-cn-off.ico` / `.png` | 已停止/异常（红色）托盘图标 |
| `tools/make_tray_icon.py` | 根据 `docs/logo.svg` 几何参数重新生成上述图标 |
| `requirements-tray.txt` | 托盘额外依赖：`pystray`、`Pillow` |

---

## 3. 前置条件

- Windows 10 / 11
- 已按主 README 创建好 `.venv` 并安装 relay 本体依赖（`requirements.txt`）
- 额外安装托盘依赖：

```bat
.venv\Scripts\pip install -r requirements-tray.txt
```

- `.env` 已配置好（至少 `PORT`；默认 `8000`）。可参考 `.env.example`。

> `启动服务.vbs` 会在启动时检查 `.venv\Scripts\pythonw.exe` 是否存在，
> 缺失会弹窗提示你先创建虚拟环境。

---

## 4. 使用

### 4.1 启动

双击 `启动服务.bat`（或直接 `启动服务.vbs`）。启动后右下角系统托盘出现图标：

- **绿色**：relay 已在 `PORT` 端口就绪；
- **红色**：未就绪或异常（多为端口被占用或启动失败，右键「查看日志」）。

首次启动时 `tray_app.py` 会：

1. 占一个本机单实例锁（默认 `127.0.0.1:8766`，可用环境变量
   `TRAY_CONTROL_PORT` 修改）——防止重复启动多个托盘；
2. 若端口空闲则拉起 `uvicorn src.main:app`；
3. 轮询 `http://127.0.0.1:PORT/v1/status` 直到就绪（最长 45 秒）。

### 4.2 托盘菜单

| 菜单项 | 行为 |
|---|---|
| **打开控制台**（默认项，左键单击图标） | 复用已打开的控制台：控制台是当前标签就聚焦其窗口，在后台标签则经 UI Automation 切换到该标签再聚焦；都找不到才新开 `http://127.0.0.1:PORT/web/login` |
| **启动/停止授权助手** | 启停 `web_login.py`（网页授权回调监听，端口 `WEB_LOGIN_LISTENER_PORT`，默认 `8765`） |
| **重启服务** | 停止并重新拉起 relay（在后台线程执行，界面不卡） |
| **查看日志** | 用系统默认程序打开 `logs/relay.log` |
| **退出** | 彻底退出：停止授权助手与 relay，并立即移除托盘图标（大退） |

### 4.3 调试

双击 `启动服务-调试.bat`，会保留一个命令行窗口并实时输出托盘日志，便于排查。

---

## 5. 运行机制（重要）

理解以下几点能帮你判断异常：

### 5.1 进程模型：venv 启动器是"两层"

Windows 上 `.venv\Scripts\python.exe` 不是真正的解释器，而是一个
**启动器 stub**，它会再派生一个真正的解释器作为子进程。也就是说，从托盘
拉起一次 relay 实际上会有 **两个** Python 进程：

```
pythonw.exe (托盘 stub)
  └─ pythonw.exe (托盘真实进程)
       └─ python.exe (relay stub)
            └─ python.exe (真正跑 uvicorn 的进程，持有端口)
```

托盘**不依赖**自己的 Popen PID 来判断服务，而是通过
「端口监听者扫描 + 命令行匹配」定位真正持有端口的进程。这样即使 stub 已经
退出、真正的进程变成孤儿，也能被找到并清理。

### 5.2 为什么会有"假死"，以及如何避免

在 Windows 上，若旧 relay 进程没被彻底清掉、新进程又抢绑同一端口，会出现
"进程还活着、端口却不再接收连接"的现象，日志里表现为：

```
ERROR:asyncio:Accept failed on a socket
socket: <asyncio.TransportSocket ... laddr=('0.0.0.0', 8000)>
OSError: [WinError 64] 指定的网络名不再可用。
```

原因有两个叠加：

1. uvicorn 在任何平台都会无条件设置 `SO_REUSEADDR`；在 Windows 上这会**允许
   两个 socket 静默绑定同一端口**，而不是报错；
2. Windows 的 asyncio accept 循环一旦遇到 `OSError`，会**关闭监听 socket 且
   不再重新挂载**，于是服务名存实亡。

托盘的规避手段：**绑定前先确保端口无人监听**（清理本端口的残留 Python 进程
并等待端口释放），从根上避免双绑。健康检查方面，只有连续多次探测失败才触发
自愈，避免单次抖动误重启。

### 5.3 自愈

后台线程每 5 秒探测 `/v1/status`：

- 探测成功 → 图标绿色；
- 连续失败达到阈值（默认 3 次）**且确认 relay 进程/端口已真正失联** →
  记录日志、清理残留、重新拉起 relay，并等待就绪。

> **探测超时与「假死」区分**：`/v1/status` 与聊天请求跑在同一个 uvicorn 事件
> 循环里。当 relay 正在解析超大请求体（数 MB）时，探测可能短暂超时，但这
> **不代表 relay 已死**。因此探测超时放宽到 10 秒（`TRAY_HEALTH_TIMEOUT`），
> 并且只要 relay 的进程或监听端口仍在，托盘**不会**强杀重启，而是等到其
> **持续无响应**超过 180 秒（`TRAY_HEALTH_HARD_SECONDS`）后才强制恢复。
> 这避免了「探测抖动 → 误杀健康 relay → 重拉 → 再次抖动」的重启循环。
> 相关阈值可用环境变量 `TRAY_HEALTH_TIMEOUT`（秒）、
> `TRAY_HEALTH_MISSES`（连续失败次数）、`TRAY_HEALTH_HARD_SECONDS`（秒）调整。

> 自愈只针对**非主动**的失联。菜单「退出」会先置停止标志，自愈逻辑随即
> 停止工作，因此退出后服务不会被重新拉起（这正是早期版本"退出后服务又
> 自己起来"问题的修复点）。

---

## 6. 与网页授权（web-login）的配合

relay 的网页授权需要一个本机监听器接收 Trae 的回调（Trae 强制回调地址为
`http://127.0.0.1:<port>/authorize`）。托盘菜单的「启动授权助手」即在后台运行
`web_login.py --relay http://127.0.0.1:PORT --port 8765`。

授权助手端点：

- `GET http://127.0.0.1:8765/healthz` — 健康检查（供网页检测）
- `GET http://127.0.0.1:8765/relay-url` — 返回配置的 relay 地址

> 上游 README 也介绍了 `start_auth.bat` 这种手动方式。托盘只是把它集成进菜单，
> 两者等价，任选其一。

---

## 7. 常见问题

**Q：托盘图标一直是红色 / 弹窗说"未在 PORT 端口就绪"。**
多为端口被占用或 relay 启动失败。右键「查看日志」看 `logs/relay.log`；也可用
`启动服务-调试.bat` 看实时输出。若端口被**非 Python 程序**占用，托盘会拒绝
强杀该程序，日志中会出现 `port <PORT> held by foreign pid=... image=...`。

**Q：我之前手动 `uvicorn` 起过 relay，现在双击托盘会怎样？**
托盘在绑定前会清理本端口上的 Python 监听进程并等待端口释放，然后重新拉起。
但稳妥起见，建议先自行结束手动启动的进程，避免混淆。

**Q：能同时用 Docker 和托盘吗？**
不建议。它们都会占用同一个 `PORT`。二选一即可。

**Q：多处弹窗/多套托盘？**
托盘有单实例锁（`TRAY_CONTROL_PORT`，默认 8766）。若第二个实例启动，会提示
"托盘已在运行"并直接打开控制台，不会再拉一套服务。

**Q：想换成自己的图标？**
修改 `docs/logo.svg` 后运行 `.venv\Scripts\python.exe tools\make_tray_icon.py`
重新生成 `assets/` 下的 `.png` 与多尺寸 `.ico`。

**Q：退出托盘后服务还在吗？**
不在。菜单「退出」会同时停止授权助手与 relay，并把端口释放。

---

## 8. 与上游的差异

本 Fork 相对上游的改动：

- **新增**（托盘机制）：`tray_app.py`、`启动服务.bat` / `启动服务.vbs` /
  `启动服务-调试.bat`、`assets/`、`requirements-tray.txt`、
  `tools/make_tray_icon.py`、本文件、README 的托盘章节与 fork 说明；
- **修复/本地化**：`src/trae_client.py`、`src/trae_remote_client.py` 的远程
  基础地址修复（新增 `_remote_web_base`），`start_auth.bat` / `web_login.py`
  的回调地址默认改为 `http://127.0.0.1:8000`，以及对应的测试
  `tests/test_trae_client.py`。

同步上游更新：

```bash
git fetch upstream
git merge upstream/main
```

> 本 Fork 的托盘机制与上游 Docker 部署互不冲突；若上游日后合入 Windows 相关
> 改动，请留意冲突文件主要出现在 `README.md`。

---

## 9. 卸载托盘机制

只需在托盘菜单「退出」，然后删除以下文件/目录即可回到上游形态：

```
tray_app.py
启动服务.bat  启动服务.vbs  启动服务-调试.bat
assets/
requirements-tray.txt
tools/make_tray_icon.py
```

随后照主 README 用 `uvicorn src.main:app --host 0.0.0.0 --port 8000` 启动即可。
