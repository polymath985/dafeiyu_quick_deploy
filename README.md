# 从零部署大肥鱼：Windows → Ubuntu

## 1. 填写密钥

首次使用，把 `key.example.txt` 复制为 `key.txt`，用记事本以 UTF-8 保存。每行 `名称=值`，不要加引号；密码里有 `=`、`#` 不需要转义。已收到填写好的 `key.txt` 时直接编辑。

| 字段 | 怎么填 |
|---|---|
| `SSH_HOST` | 服务器公网 IPv4 或域名 |
| `SSH_USER` | 建议使用 `root`；其他用户需要免密 sudo 权限 |
| `SSH_PASSWORD` | SSH 登录密码 |
| `ASTRBOT_USERNAME` | 想设置的 AstrBot 后台账号 |
| `ASTRBOT_PASSWORD` | 至少 8 位，包含大写、小写字母及数字 |
| `OWNER_QQ` | 可选，你的大号 QQ；空着时不设置管理员，也不主动私聊 |
| `DEEPSEEK_API_KEY` | DeepSeek 开放平台 API Key，需有可用额度 |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash`；按平台实际提供的模型填写 |
| `SILICONFLOW_API_KEY` | 可选，用于 `BAAI/bge-m3` 云端记忆；空着跳过记忆 |
| `DOUBAO_APP_ID` | 可选，火山引擎豆包语音 App ID |
| `DOUBAO_ACCESS_TOKEN` | 可选，同一语音应用的 Access Token，只填凭证值，不包含 `key:` 标签；不是火山方舟 API Key |
| `NAPCAT_TOKEN` | 留空，启动并验证后台后自动回填实际 Token |
| `ONEBOT_TOKEN` | 留空，自动生成；这是两个容器之间的独立连接凭证 |

豆包语音旧版界面：[打开旧版控制台](https://console.volcengine.com/speech/service/10035?AppID=2320335936)。链接中的 `AppID` 为示例，请切换到自己的应用，并填写同一应用的 App ID 和 Access Token。

**豆包两项必须同时填写。任一为空就跳过语音和唱歌，不安装相关插件，不调用语音接口，也不会偷偷改用其他语音服务。** 机器人 QQ 号码和 QQ 密码都不需要填写，最后由用户自行扫码。

在阿里云安全组入方向放行 TCP **6099（NapCat）**、**6185（AstrBot）**，并保留 SSH 默认端口 **22**。
## 2. Windows 一键部署

在 PowerShell 中打开 `dafeiyu_quick_deploy` 文件夹，然后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\upload.ps1
```

脚本会直接读取 `key.txt` 中的 `SSH_PASSWORD`，上传、连接服务器和同步 Token 都无需再次输入密码。SSH 固定使用默认端口 22；密码不会显示在终端或放进命令行。仅使用 Windows 自带的 tar、OpenSSH 和 .NET Framework，不需要本机安装 Python。

保留同目录下的 `ssh-askpass.cs`：脚本会在 Windows 系统临时目录编译密码读取助手，并在退出时自动清理助手、传输包等临时文件。项目目录中只保留发布用文件。

如果提示缺少 OpenSSH Client，用**管理员 PowerShell**安装 Windows 官方可选功能，再重跑：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

成功后，文件位于服务器当前 SSH 用户的 `~/dafeiyu`。只上传 `deploy.sh`、`configure.py`、`compose.yaml`、`pack/` 和填写好的 `key.txt`。SSH 主机身份记录使用 Windows 用户目录下标准的 `.ssh/known_hosts`，不会随部署包分发。

上传完成后，脚本会立即通过 SSH 执行 Ubuntu 部署命令，并在当前 PowerShell 窗口显示进度。部署成功后，自动将 NapCat 和 OneBot Token 同步回本机 `key.txt`。全程保持窗口开启，无需另开 Ubuntu 终端，也无需手动执行部署命令。

重装服务器系统后，SSH 主机密钥会变化。如果同一 IP 提示 `REMOTE HOST IDENTIFICATION HAS CHANGED`，确认是自己重装的服务器后，运行以下命令删除该 IP 的旧记录，再重试上传：

```powershell
ssh-keygen -R 你的服务器IP
```

## 3. 登录后台并扫码

脚本依次：配置国内软件源 → 安装 Docker/Compose → 生成持久化配置和设备标识 → 下载并校验固定镜像 → 启动两个服务 → 回填 NapCat Token → 导入人格、插件、记忆/可选语音 → 检查服务。

Ubuntu 软件包使用阿里云源，Docker 安装包使用南大镜像源。首次下载镜像、插件依赖和表情包需要数分钟到十几分钟，按实际网络而定。出现下载错误时按输出修正后重跑原命令，已有镜像层、插件、数据和 QQ 会话会复用。成功部署且密钥与配置包未变时，重复运行会跳过配置导入及收费模型测试。

服务器部署时会生成 `data/`、`napcat/` 和 `state/`，分别存放机器人数据、QQ 会话及部署状态。这些运行数据不包含在发布包中，每位用户部署时自行生成。

完成后打开：

- AstrBot：`http://你的服务器IP:6185/`，使用 `key.txt` 中的 AstrBot 账号密码。
- NapCat：`http://你的服务器IP:6099/webui/web_login`，使用自动回填到本机 `key.txt` 的 `NAPCAT_TOKEN`。

脚本不会把 Token 打印到录屏终端。**此时 QQ 尚未登录是正常状态**。用户进入 NapCat 后，用机器人小号的手机 QQ 扫码、按腾讯提示确认。二维码失效就在网页刷新。随后从自己的 QQ 发一句话测试回复。

部署报错时，按终端提示修正后重跑同一个命令。需要单独连接服务器或重新同步 Token 时，可分别给 `upload.ps1` 加上 `-Connect` 或 `-SyncKey`；正常部署会自动完成 Token 同步。
