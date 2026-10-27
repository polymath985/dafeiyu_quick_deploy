# 给 AI agent：使用现有脚本部署大肥鱼

## 目标与范围

将 `dafeiyu_quick_deploy` 目录部署到 `key.txt` 所指的新 Ubuntu 26.04 LTS amd64 服务器，从零安装并启动 AstrBot、NapCat，配置 DeepSeek、大肥鱼人格、插件、可选云端记忆和语音。**不要迁移旧实例，不修改 AstrBot/NapCat 核心源码，不替用户登录 QQ。** QQ 登录留在 NapCat WebUI，由用户本人扫码、处理腾讯验证。

所有部署逻辑已经在本目录脚本中。先读 `README.md`、`key.example.txt`、`deploy.sh`、`configure.py`，然后使用脚本；不要另写一套安装流程或改成 Windows 本地 QQ 部署。

## 密钥约定

`key.txt` 是 UTF-8 的 `名称=值` 文本，按第一个等号分隔，忽略空行和 `#` 开头的注释。值无需引号或 shell 转义。**不要 source/eval 此文件，不把密码/Token 放进 shell 参数、聊天、录屏或日志。** 读取时只报告某字段是否已提供，不打印内容。只分享 `key.example.txt`，不分享实际 `key.txt`。

- SSH 只读取 `SSH_HOST/SSH_USER/SSH_PASSWORD`，固定使用默认端口 22 和密码登录，用户无需填写端口。
- `OWNER_QQ` 允许留空；此时继续部署，不猜号码，不把机器人号码当主人，也不授予任何人管理员权限，主动私聊暂不启用。
- `SILICONFLOW_API_KEY` 空着就跳过记忆。
- `DOUBAO_ACCESS_TOKEN` 只填实际 Token，不能把原始说明中的 `key:` 标签并入凭证。
- **只有 `DOUBAO_APP_ID`、`DOUBAO_ACCESS_TOKEN` 同时非空才启用语音和唱歌。** 任一缺失都自动跳过，不请求补填、不安装语音/唱歌插件、不使用其他 TTS 兜底。
- `NAPCAT_TOKEN/ONEBOT_TOKEN` 初始可空，由脚本生成或读取实际配置并回填。二者用途不同，不应混用。

## 执行步骤

1. 检查字段是否齐全，确认目标是新服务器。必填缺失时一次列出缺失字段；主人 QQ、记忆和语音凭证属于可选项。
2. 首选在 Windows 运行一次 `powershell -NoProfile -ExecutionPolicy Bypass -File .\upload.ps1`。它会上传到 `~/dafeiyu`，随即通过 SSH 运行部署命令，成功后自动把 Token 同步回本机。无需另开 Ubuntu 终端，也不要重复执行下面的手工部署步骤。脚本自动读取 SSH 密码；用户使用 `root`，或具有免密 sudo 权限的账户。它使用 Windows 自带 tar、OpenSSH 和 .NET Framework；密码通过助手的专用管道提供，不写入命令行或日志。
3. **仅在不使用 Windows 一键入口时**，agent 可通过已有 SSH/SFTP 工具的凭证参数连接。先运行 `upload.ps1 -PrepareOnly`，按输出取得 Windows 系统临时目录中的安装包，用 SFTP 上传为 `~/dafeiyu-upload.tar.gz`，再执行下列解压命令。传输后删除本地临时安装包；它包含真实凭证。没有 SFTP 工具但运行环境已提供 Python/Paramiko 时，也可以用它传输；不要把本机必须安装 Python 变成普通用户的前提。

   ```bash
   umask 077
   mkdir -p ~/dafeiyu
   tar -xzf ~/dafeiyu-upload.tar.gz -C ~/dafeiyu
   chmod 600 ~/dafeiyu/key.txt
   rm -f ~/dafeiyu-upload.tar.gz
   ```

   SSH 主机密钥验证必须保留，使用 Windows 用户目录下标准的 `.ssh/known_hosts`，不在项目中创建或分发主机记录。自动密码助手只回答密码请求，不回答主机密钥确认；首次连接采用 `accept-new`。用户确认重装了同一 IP 的服务器时，核对新指纹后用 `ssh-keygen -R 服务器IP` 移除该 IP 的旧记录，再重新连接。
4. 采用上一步手工传输方式时，在 Ubuntu 运行：

   ```bash
   cd ~/dafeiyu
   sudo bash deploy.sh
   ```

   agent 工具单次执行时间短时，可将原脚本放后台运行，日志限定在私人目录；之后读取尾部了解进度，避免因工具超时重启服务：

   ```bash
   cd ~/dafeiyu
   mkdir -p state
   chmod 700 state
   nohup sudo bash deploy.sh >state/deploy.log 2>&1 </dev/null &
   ```

5. 首次下载通常耗时较长，按 `[1/5]` 至 `[5/5]` 输出判断进度。Ubuntu 软件源使用阿里云，Docker 安装包使用南大镜像，容器镜像使用 DaoCloud 后回退官方源；签名、摘要、TLS 校验不能关闭。失败后先看实际错误，修正网络问题再重跑同一脚本。不要反复删除数据或重新创建容器来尝试 QQ 登录。
6. 提醒用户在阿里云安全组放行 TCP 6099、6185。脚本会在已启用的 UFW 上放行这两个端口，保留原有规则；云安全组需要用户或已有云工具处理。无需公网开放 6199。
7. 运行 `sudo bash deploy.sh --check`。检查两个后台可用、预期插件激活、没有加载失败、OneBot 地址/Token 一致、NapCat Token 与服务器 `key.txt` 一致。`state/verification.json` 保存检查结果，不包含密钥。`state/` 与 `data/`、`napcat/` 都是在服务器运行时生成的目录，不在发布包中携带历史数据。
8. Windows 一键入口会在部署成功后自动回填 Token。手工部署或需要重新同步时，再运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\upload.ps1 -SyncKey`。用 SFTP 的 agent 可下载服务器 `key.txt` 到私人临时路径，再仅合并 `NAPCAT_TOKEN`、`ONEBOT_TOKEN` 两行；不要覆盖本机其他新编辑的字段。完成后校验本地值与服务器当前配置相同，只报告相同与否，不打印值。
9. 向用户给出 `http://服务器IP:6185/` 和 `http://服务器IP:6099/webui/web_login`，说明 AstrBot 登录使用已填账号密码、NapCat 使用 `key.txt` 中的 `NAPCAT_TOKEN`，并提示现在自行扫码。

## 验收边界

- `services_ready=true` 表示后台、配置和插件检查通过。
- 用户尚未扫码时 `qq_logged_in=false` 是待用户操作状态，不是安装失败。不要循环重启、密码登录或自动扫描二维码。
- 不发送真实 QQ 消息来测试；需要用户在手机端发消息验收，不把 HTTP 200 或模型测试冒充 QQ 端到端成功。
- 首次完整配置会进行小额 DeepSeek/向量/已启用语音的接口测试。`--check` 不调用收费模型；成功且密钥/配置包未变时重跑会跳过重复导入与收费测试。
- 已填写的可选语音测试失败时，脚本保留凭证并关闭语音/唱歌，继续验证基础部署。必须向用户说明语音失败，不能把 `voice_enabled=false` 写成语音成功；修正外部服务权限后用 `--configure` 显式重试。
- 以后用户补填主人 QQ 或语音凭证后，重新上传 `key.txt`，运行 `sudo bash deploy.sh --configure`。它会按配置包重新应用设置，不应在用户没有要求时覆盖其 WebUI 自定义设置。
- 任何环节失败都要报告具体未完成项，不将“容器已启动”写成“完整机器人部署成功”。最终说明服务状态、两个访问地址、Token 已回填与否、待用户扫码的状态即可。

本目录的 `pack/` 是已有大肥鱼配置包的部署用子集，保留其许可证和自带插件，复用 `pack/tools/import_dafeiyu.py`。适配仅涉及部署工具：允许主人 QQ 暂空、缺少凭证时跳过可选功能、导入失败返回失败状态。第三方插件由 AstrBot 插件市场安装，表情包由资源广场下载。
