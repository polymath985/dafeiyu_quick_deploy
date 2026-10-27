"""Configure a fresh deployment using files and AstrBot's supported APIs."""

import hashlib
import importlib.util
import json
import os
import re
import secrets
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICE_URL_LINE = re.compile(r"(?:AstrBot|NapCat):\s*https?://\S+", re.IGNORECASE)


def keys():
    result = {}
    for line in (ROOT / "key.txt").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if SERVICE_URL_LINE.fullmatch(line.strip()):
            continue
        name, separator, value = line.partition("=")
        if not separator or name.strip() in result:
            raise ValueError("key.txt 存在无效行或重复字段，请按 key.example.txt 填写。")
        result[name.strip()] = value.strip()
    return result


def validate(k):
    for name in ("SSH_HOST", "ASTRBOT_USERNAME", "ASTRBOT_PASSWORD", "DEEPSEEK_API_KEY"):
        if not k.get(name):
            raise ValueError(f"key.txt 缺少 {name}")
    password = k["ASTRBOT_PASSWORD"]
    if len(password) < 8 or not all(re.search(p, password) for p in ("[A-Z]", "[a-z]", "[0-9]")):
        raise ValueError("ASTRBOT_PASSWORD 至少 8 位，且包含大写、小写字母和数字。")
    if k.get("OWNER_QQ") and not k["OWNER_QQ"].isdigit():
        raise ValueError("OWNER_QQ 应为你的大号 QQ，或者留空。")
    if re.match(r"(?i)^key\s*[:：]", k.get("DOUBAO_ACCESS_TOKEN", "")):
        raise ValueError("DOUBAO_ACCESS_TOKEN 只填写 Token 本身，不要包含 key: 字段标签。")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def update_keys(updates, write_urls=False):
    path = ROOT / "key.txt"
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if write_urls:
        host = keys()["SSH_HOST"]
        lines = [line for line in lines if not SERVICE_URL_LINE.fullmatch(line.strip())]
    pending = dict(updates)
    for index, line in enumerate(lines):
        name = line.partition("=")[0].strip()
        if not line.lstrip().startswith("#") and name in pending:
            lines[index] = f"{name}={pending.pop(name)}"
    lines.extend(f"{name}={value}" for name, value in pending.items())
    if write_urls:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", f"AstrBot: http://{host}:6185/", f"NapCat:  http://{host}:6099/webui/web_login"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def init():
    k = keys()
    validate(k)
    for name in ("data", "napcat/config", "napcat/qq", "napcat/plugins", "state"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    identity = ROOT / "napcat/machine-id"
    if not identity.exists():
        identity.write_text(uuid.uuid4().hex + "\n", encoding="ascii")
    env = ROOT / ".env"
    if not env.exists():
        tail = identity.read_text().strip()[:10]
        env.write_text("NAPCAT_MAC=02:" + ":".join(tail[i:i + 2] for i in range(0, 10, 2)) + "\n", encoding="ascii")
        env.chmod(0o600)
    onebot_path = ROOT / "napcat/config/onebot11.json"
    if onebot_path.exists():
        onebot_token = json.loads(onebot_path.read_text())["network"]["websocketClients"][0]["token"]
    else:
        onebot_token = k.get("ONEBOT_TOKEN") or secrets.token_urlsafe(32)
        write_json(onebot_path, {"network": {
            "httpServers": [], "httpClients": [], "websocketServers": [],
            "websocketClients": [{"name": "AstrBot", "enable": True,
                "url": "ws://astrbot:6199/ws", "messagePostFormat": "array",
                "token": onebot_token, "reportSelfMessage": False,
                "enableForcePushEvent": True, "debug": False,
                "heartInterval": 30000, "reconnectInterval": 30000}]},
            "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False})
    update_keys({"ONEBOT_TOKEN": onebot_token})
    webui = ROOT / "napcat/config/webui.json"
    if not webui.exists():
        write_json(webui, {"host": "0.0.0.0", "port": 6099, "prefix": "",
            "token": k.get("NAPCAT_TOKEN") or secrets.token_urlsafe(32), "loginRate": 3})
    config = ROOT / "data/cmd_config.json"
    if config.exists():
        print("保留已有业务配置与 QQ 会话；不会重置已有账号密码。", flush=True)
        return
    salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", k["ASTRBOT_PASSWORD"].encode(), bytes.fromhex(salt), 600000).hex()
    model = "deepseek/" + (k.get("DEEPSEEK_MODEL") or "deepseek-flash")
    write_json(config, {"config_version": 5,
        "dashboard": {"enable": True, "username": k["ASTRBOT_USERNAME"],
            "password": hashlib.md5(k["ASTRBOT_PASSWORD"].encode()).hexdigest(),
            "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${password_hash}",
            "password_storage_upgraded": True, "password_change_required": False,
            "jwt_secret": secrets.token_hex(32), "host": "0.0.0.0", "port": 6185},
        "platform": [{"id": "大肥鱼", "type": "aiocqhttp", "enable": True,
            "ws_reverse_host": "0.0.0.0", "ws_reverse_port": 6199, "ws_reverse_token": onebot_token}],
        "provider_sources": [{"id": "deepseek", "provider": "deepseek",
            "type": "openai_chat_completion", "provider_type": "chat_completion",
            "key": [k["DEEPSEEK_API_KEY"]], "api_base": "https://api.deepseek.com/v1",
            "timeout": 120, "proxy": "", "custom_headers": {}, "enable": True}],
        "provider": [{"id": model, "provider_source_id": "deepseek",
            "enable": True, "model": model.split("/", 1)[1],
            "modalities": ["text", "tool_use"],
            "custom_extra_body": {"thinking": {"type": "disabled"}}}],
        "agent_runner": {"type": "local", "config": {"model": {"provider_id": model}}},
        "platform_settings": {"enable_id_white_list": False},
        "provider_settings": {"computer_use_runtime": "none", "streaming_response": False},
        "admins_id": [k["OWNER_QQ"]] if k.get("OWNER_QQ") else []})
    print("已生成全新配置；后台密码已哈希，OneBot 仅在容器内网监听。", flush=True)


def request(base, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path,
        data=None if data is None else json.dumps(data).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def sync():
    path = ROOT / "napcat/config/webui.json"
    for attempt in range(90):
        try:
            config = json.loads(path.read_text())
            credential = request("http://127.0.0.1:6099/api", "/auth/login", {
                "hash": hashlib.sha256((config["token"] + ".napcat").encode()).hexdigest()
            })["data"]["Credential"]
            if not credential:
                raise ValueError("NapCat authentication failed")
            update_keys({"NAPCAT_TOKEN": config["token"]})
            print("NapCat 后台已可用，实际 Token 已写回服务器 key.txt（不在终端显示）。", flush=True)
            return
        except (OSError, ValueError, KeyError, TypeError):
            if attempt == 89:
                raise RuntimeError("NapCat 启动超时，请查看 docker compose logs --tail=50 napcat。") from None
            time.sleep(2)


def importer():
    spec = importlib.util.spec_from_file_location("dafeiyu_import", ROOT / "pack/tools/import_dafeiyu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def login(module):
    k = keys()
    for attempt in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:6185/", timeout=3).close()
            break
        except OSError:
            if attempt == 119:
                raise RuntimeError("AstrBot 启动超时，请查看 docker compose logs --tail=50 astrbot。") from None
            time.sleep(2)
    api = module.Api("http://127.0.0.1:6185")
    if not api.login(k["ASTRBOT_USERNAME"], k["ASTRBOT_PASSWORD"]):
        raise RuntimeError("AstrBot 登录失败，请核对 key.txt；不会自动重置已有密码。")
    return api


def configuration_signature(k):
    signature = hashlib.sha256()
    signature.update(json.dumps({name: k.get(name, "") for name in (
        "OWNER_QQ", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "SILICONFLOW_API_KEY",
        "DOUBAO_APP_ID", "DOUBAO_ACCESS_TOKEN")}, sort_keys=True).encode())
    for path in sorted((ROOT / "pack").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            signature.update(path.relative_to(ROOT / "pack").as_posix().encode())
            signature.update(path.read_bytes())
    return signature.hexdigest()


def apply():
    k = keys()
    validate(k)
    m = importer()
    api = login(m)
    voice_requested = bool(k.get("DOUBAO_APP_ID") and k.get("DOUBAO_ACCESS_TOKEN"))
    voice_digest = hashlib.sha256((k.get("DOUBAO_APP_ID", "") + "\n" + k.get("DOUBAO_ACCESS_TOKEN", "")).encode()).hexdigest()
    failed_voice = ROOT / "state/voice-failed.sha256"
    cached_failure = failed_voice.exists() and failed_voice.read_text().strip() == voice_digest
    voice = voice_requested and not cached_failure
    memory = bool(k.get("SILICONFLOW_API_KEY"))
    signature = configuration_signature(k)
    marker = ROOT / "state/configured.sha256"
    if marker.exists() and marker.read_text().strip() == signature:
        print("配置与上次成功部署一致，跳过重复导入和收费模型测试。", flush=True)
        return
    if voice_requested and cached_failure:
        print("本组语音凭证上次测试失败，保持关闭；修正权限后用 --configure 显式重试。", flush=True)
    elif not voice:
        print("豆包 App ID 或 Access Token 为空：跳过语音和唱歌，不安装其插件。", flush=True)
    if not k.get("OWNER_QQ"):
        print("主人 QQ 留空：暂不设置管理员，不启用主动私聊；基础聊天正常配置。", flush=True)
    if voice:
        existing = {p["name"] for p in api.call("GET", "/plugins")["data"]}
        for name in ("astrbot_plugin_seed_audio_tts", "astrbot_plugin_dafeiyu_sing"):
            if name in existing:
                result = api.call("PATCH", "/plugins/enabled", {"plugin_id": name, "enabled": True})
                if not api.ok(result):
                    raise RuntimeError("无法启用语音插件：" + name)
    config = api.call("GET", "/config-profiles/default")["data"]["config"]
    source = next(p for p in config["provider_sources"] if p["id"] == "deepseek")
    source["key"] = [k["DEEPSEEK_API_KEY"]]
    # Provider-source edits through the configuration API are applied at runtime.
    model = "deepseek/" + (k.get("DEEPSEEK_MODEL") or "deepseek-flash")
    provider = next((p for p in config["provider"] if p.get("provider_source_id") == "deepseek"), None)
    if provider:
        provider.update(id=model, model=model.split("/", 1)[1])
    config["agent_runner"]["config"]["model"]["provider_id"] = model
    result = api.call("PUT", "/config-profiles/default", config)
    if not api.ok(result):
        raise RuntimeError("无法更新基础模型配置：" + api.msg(result))
    m.ANSWERS = {"base_url": "http://127.0.0.1:6185", "username": k["ASTRBOT_USERNAME"],
        "password": k["ASTRBOT_PASSWORD"], "owner_qq": k.get("OWNER_QQ", ""),
        "memory": "2" if memory else "0", "siliconflow_key": k.get("SILICONFLOW_API_KEY", ""),
        "voice": "3" if voice else "0", "volc_app_id": k.get("DOUBAO_APP_ID", ""),
        "volc_access_token": k.get("DOUBAO_ACCESS_TOKEN", "")}
    imported = m.main()
    failures = {name for name, good in getattr(m, "LAST_RESULTS", []) if not good}
    if "语音" in failures:
        failed_voice.write_text(voice_digest + "\n", encoding="ascii")
    elif voice and failed_voice.exists():
        failed_voice.unlink()
    if not imported and (not failures or failures - {"语音"}):
        raise RuntimeError("部分导入项目失败，修正输出中的原因后重跑 sudo bash deploy.sh --configure。")
    voice_ready = voice and "语音" not in failures
    if voice and not voice_ready:
        print("可选语音测试失败：保留原凭证，关闭语音和唱歌，继续完成基础部署。修正权限后用 --configure 重试。", flush=True)
    if not voice_ready:
        existing = {p["name"] for p in api.call("GET", "/plugins")["data"]}
        for name in ("astrbot_plugin_seed_audio_tts", "astrbot_plugin_dafeiyu_sing"):
            if name in existing:
                result = api.call("PATCH", "/plugins/enabled", {"plugin_id": name, "enabled": False})
                if not api.ok(result):
                    raise RuntimeError("无法停用旧语音插件：" + name)
    good, _ = m.test_provider(api, model)
    if not good:
        raise RuntimeError("DeepSeek 测试未通过，请核对 API Key、模型名称与账户余额。")
    marker.write_text(signature + "\n", encoding="ascii")
    write_json(ROOT / "state/features.json", {"voice_requested": voice_requested,
        "voice_enabled": voice_ready, "voice_test_failed": cached_failure or "语音" in failures})
    print("基础模型与配置导入成功。", flush=True)


def check():
    k = keys()
    m = importer()
    api = login(m)
    assert (ROOT / "state/configured.sha256").is_file(), "配置导入尚未全部成功"
    plugins = api.call("GET", "/plugins")["data"]
    voice_requested = bool(k.get("DOUBAO_APP_ID") and k.get("DOUBAO_ACCESS_TOKEN"))
    config = api.call("GET", "/config-profiles/default")["data"]["config"]
    voice = voice_requested and any(p.get("type") == "seed_audio_tts" and p.get("enable") for p in config["provider"])
    memory = bool(k.get("SILICONFLOW_API_KEY"))
    expected = {"astrbot_plugin_group_chat_plus", "meme_manager", "astrbot_plugin_qzone",
        "astrbot_plugin_qqprofile", "astrbot_plugin_proactive_chat", "astrbot_plugin_dafeiyu_signature",
        "astrbot_plugin_dafeiyu_clean"}
    if memory:
        expected.add("astrbot_plugin_livingmemory")
    if voice:
        expected.update({"astrbot_plugin_seed_audio_tts", "astrbot_plugin_dafeiyu_sing"})
    active = {p["name"] for p in plugins if p.get("activated")}
    assert expected <= active, "部分插件未激活：" + ", ".join(sorted(expected - active))
    assert not api.call("GET", "/plugins/failed").get("data"), "存在加载失败的插件"
    if not voice:
        assert not {"astrbot_plugin_seed_audio_tts", "astrbot_plugin_dafeiyu_sing"} & active, "可选语音应处于关闭状态"
    onebot = json.loads((ROOT / "napcat/config/onebot11.json").read_text())["network"]["websocketClients"][0]
    platform = next(p for p in config["platform"] if p["type"] == "aiocqhttp")
    assert platform["ws_reverse_token"] == onebot["token"] == k["ONEBOT_TOKEN"], "OneBot Token 不一致"
    assert platform["ws_reverse_host"] == "0.0.0.0" and onebot["url"] == "ws://astrbot:6199/ws", "OneBot 地址不一致"
    nap_config = json.loads((ROOT / "napcat/config/webui.json").read_text())
    assert nap_config["token"] == k["NAPCAT_TOKEN"], "NapCat Token 未同步"
    base = "http://127.0.0.1:6099/api"
    token = request(base, "/auth/login", {"hash": hashlib.sha256((k["NAPCAT_TOKEN"] + ".napcat").encode()).hexdigest()})["data"]["Credential"]
    status = request(base, "/QQLogin/CheckLoginStatus", {}, token)["data"]
    report = {"checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services_ready": True, "plugins_active": len(expected), "voice_requested": voice_requested, "voice_enabled": voice,
        "memory_enabled": memory, "owner_configured": bool(k.get("OWNER_QQ")),
        "qq_logged_in": bool(status.get("isLogin") and status.get("coreReady")),
        "napcat_token_saved": True, "qq_delivery_tested": False}
    write_json(ROOT / "state/verification.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    print(f"AstrBot：http://{k['SSH_HOST']}:6185/", flush=True)
    print(f"NapCat：http://{k['SSH_HOST']}:6099/webui/web_login", flush=True)
    print("QQ 已登录；本次检查未发送 QQ 消息。" if report["qq_logged_in"] else "等待用户用机器人 QQ 扫码登录；未执行 QQ 登录或发送消息。", flush=True)
    update_keys({}, write_urls=True)


if __name__ == "__main__":
    os.umask(0o077)
    actions = {"init": init, "sync": sync, "apply": apply, "check": check}
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in actions:
            raise ValueError("用法：python3 configure.py init|sync|apply|check")
        actions[sys.argv[1]]()
    except Exception as error:
        message = str(error)
        try:
            credentials = keys()
        except (OSError, ValueError):
            credentials = {}
        for name, value in credentials.items():
            if len(value) > 4 and any(word in name for word in ("PASSWORD", "KEY", "TOKEN")):
                message = message.replace(value, "[REDACTED]")
        print("错误：" + message, file=sys.stderr)
        sys.exit(1)
