# SideloadlyFix

Windows 下的 Sideloadly 启动更新检查连接故障修复工具。只依赖 Python 标准库及系统 PowerShell；已验证 Sideloadly **0.70.1 / Windows amd64**。

## 工作方式

对用户自行安装的官方软件制作项目内副本，将一个 30 字节的更新服务器前缀等长替换为 `http://127.0.0.1:33875/rrrrrr/`，实际改变 24 个字节。本地服务只提供与当前副本相匹配的官方更新清单，不提供账号认证响应。

新版清单不会直接覆盖正在使用的旧清单。启动时定期检查官方版本；发现新版则下载并核对安装包 SHA256，具备 7-Zip 时解压到临时目录，核对主程序和 daemon 的官方哈希，检查依赖及补丁适用性，再制作独立候选副本。进入主界面确认后才启用。旧副本与清单保留，可回滚。

## 首次使用

1. 自行下载、安装 [官方 Sideloadly](https://sideloadly.io/)。本仓库不包含第三方程序和 DLL。
2. 安装 Python 3.10 或更高版本，或使用本机已有的 Codex Python 运行时。脚本优先使用 `SIDELOADLYFIX_PYTHON` 指定的路径，其次寻找当前用户的 Codex 运行时，最后寻找 PATH 中的 Python；忽略 Windows Store 占位程序。
3. 如果网络需要代理，把 `config.example.json` 复制为 `config.json`，配置 HTTP 代理，例如 `"proxy": "http://127.0.0.1:10809"`。`null` 使用环境代理，空字符串禁用代理。不支持直接使用 SOCKS 代理。不要提交含账号密码的配置文件。
4. 初始化：

```powershell
.\prepare.cmd --source "D:\Sideloadly"
```

准备过程会读取官方三份清单并核对原版文件。原版文件与官方最新版本不一致时应先安装匹配版本，不会强行套用补丁。也可用 `--manifests-dir PATH` 指定事先取得的三份官方清单，文件名见下文。

本机已有旧版项目的 `local-app`、`patch-report.json` 和三份清单时，启动脚本会自动核对并迁移它们，无需重新初始化。

## 启动和更新

```powershell
.\start.cmd                     # 定期检查更新，然后启动当前可用版本
.\start.cmd -Offline            # 跳过更新检查，直接使用缓存
.\update.cmd --check-only       # 立即检查版本，不下载
.\update.cmd                    # 立即检查并下载新版，条件满足时制作候选副本
```

默认每 24 小时检查一次，可以在配置中调整 `check_interval_hours` 或关闭 `automatic_updates`。连接失败、官方清单版本不同步、哈希不符或补丁不适用时，保持原活动版本。错误记录在 `data/update.log`。SHA256 校验依赖通过 HTTPS 获取的官方清单，不等同于独立的数字签名验证。

新版安装包保存在 `data/downloads/版本号/`。脚本自动寻找 PATH 或标准安装目录中的完整 `7z.exe`；也可用配置字段 `extractor` 指定。需要支持 NSIS 的完整 7-Zip，精简的 `7za/7zr` 不适用。脚本不会替你安装 7-Zip，也不会执行 Sideloadly 安装包或修改系统安装。

没有解压工具时，下载、校验依然可完成，但会保留当前版本。可自行安装下载的官方新版，然后运行：

```powershell
.\update.cmd --source "新版官方安装目录"
```

有候选副本后，先关闭项目内 Sideloadly 和系统托盘 daemon，再停止旧快照的服务器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop-server.ps1
.\start.cmd -Candidate
```

确认候选版本可以进入主界面后，关闭候选及其托盘 daemon，再执行：

```powershell
.\update.cmd --activate
.\start.cmd -Offline
```

如遇问题，关闭程序及托盘 daemon，停止本地服务，再运行：

```powershell
.\update.cmd --rollback
.\start.cmd -Offline
```

启用及回滚会检测项目内进程，运行期间拒绝切换。回滚保存的是上一活动版本；连续执行回滚可在最近两个版本间切换。本地服务器固定提供启动时的清单快照；切换版本后若端口仍提供旧快照，先停止服务再启动，不会自动终止未知进程。

## 配置

`config.example.json` 包含所有默认配置。`config.json` 为本机配置，已被 Git 忽略。Python 路径可以在 PowerShell 当前会话指定：

```powershell
$env:SIDELOADLYFIX_PYTHON = 'C:\Python312\python.exe'
```

服务固定监听 `127.0.0.1:33875`，不会监听局域网接口。`/health` 用于核对项目目录、清单目录与版本。更新清单仅有以下三个路由，其他路由返回 404：

| 路由 | 缓存文件名 |
| --- | --- |
| `/rrrrrr/windows-amd64.json` | `windows-amd64.json` |
| `/rrrrrr/exe/windows-amd64.json` | `exe-windows-amd64.json` |
| `/rrrrrr/daemon/windows-amd64.json` | `daemon-windows-amd64.json` |

`data/active.json` 保存当前版本及目录；`data/candidate.json` 保存候选；`data/previous.json` 保存上一版。不要手动修改版本号、哈希或清单来强行启用新版。并发更新由操作系统文件锁阻止，状态文件采用同目录原子替换。

## 验证与限制

```powershell
python -m unittest discover -s tests -v
```

测试覆盖版本比较、清单一致性、官方哈希格式、安装包损坏、补丁适用性、候选完整性、状态切换与回滚、离线回退以及 HTTP 路由隔离。已实机验证 0.70.1 启动及官方版本检查。尚无后续官方版本，未来 NSIS 安装包布局和启动兼容性不能预先保证，因此不自动启用候选版本。账号登录、签名和安装仍需真实联网服务，未纳入本工具的验证。

补丁会改变副本文件哈希及可能的签名有效性。保留原安装不改动；如需恢复，可关闭副本后运行原官方软件。本项目与 Sideloadly、Apple 无关联，MIT 许可证仅覆盖本仓库脚本及文档，不授予第三方软件的任何权利。

仓库忽略 `local-app/`、`data/`、官方缓存清单、日志、PID、个人配置及第三方二进制。公开仓库仍须自行确认适用的官方许可和法律要求。
