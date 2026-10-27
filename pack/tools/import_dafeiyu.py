"""Deployment subset of the existing Dafeiyu configuration importer.

Uses AstrBot APIs to configure persona, plugins, memes, cloud memory and optional
Doubao voice. Local Windows discovery, Ollama and other TTS backends are omitted.
"""
import getpass
import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSWERS = {}
# 表情包插件下 GitHub 用的加速站，按顺序试；最后的空串是直连 GitHub。加速站常换常挂，挂了往这里补新的
GH_ACCELERATORS = ["https://gh-proxy.com/", "https://ghfast.top/", "https://hk.gh-proxy.com/", ""]


def say(msg=""):
    print(msg, flush=True)


def step(msg):
    say(f"\n▶ {msg}")


def warn(msg):
    say(f"  ！{msg}")


def read_secret_file(path, key=None):
    lines = open(path, encoding="utf-8-sig").read().splitlines()
    if key:
        for line in lines:
            k, sep, v = line.replace("：", ":", 1).partition(":")  # 中文冒号也认
            if sep and k.strip().lower() == key:
                return v.strip()
    return lines[0].strip()


def ask(name, prompt, default=None, secret=False, choices=None):
    """问一个问题；--answers 模式下从回答文件取。"""
    if name in ANSWERS:
        return str(ANSWERS[name])
    if f"{name}_file" in ANSWERS:
        return read_secret_file(ANSWERS[f"{name}_file"], ANSWERS.get(f"{name}_key"))
    if ANSWERS:
        if default is not None:
            return default
        sys.exit(f"回答文件里缺 {name}")
    while True:
        hint = f"（直接回车用 {default}）" if default not in (None, "") else ""
        text = f"{prompt}{hint}："
        value = getpass.getpass(text) if secret else input(text)
        value = value.strip() or (default or "")
        if choices and value not in choices:
            say(f"  请输入 {' / '.join(choices)} 里的一个")
            continue
        if value:
            return value


class Api:
    def __init__(self, base):
        self.root = base.rstrip("/")
        self.token = None

    def call(self, method, path, body=None, timeout=120, raw_body=None, content_type="application/json"):
        url = self.root + "/api/v1" + path
        data = raw_body if raw_body is not None else (
            json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", content_type)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                code = r.status
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            code = e.code
        try:
            out = json.loads(text) if text else {}
        except ValueError:
            out = {"status": "error", "message": text[:300]}
        if not isinstance(out, dict):
            out = {"status": "ok", "data": out}
        out["_http"] = code
        return out

    @staticmethod
    def ok(r):
        return r.get("_http", 200) < 400 and r.get("status", "ok") != "error"

    @staticmethod
    def msg(r):
        return str(r.get("message") or r.get("detail") or r.get("data") or r)[:300]

    def login(self, user, password):
        for pw in (password, hashlib.md5(password.encode()).hexdigest()):
            r = self.call("POST", "/auth/login", {"username": user, "password": pw})
            data = r.get("data")
            if isinstance(data, dict) and data.get("token"):
                self.token = data["token"]
                return True
        return False


def get_path(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def set_path(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def fill(value, ctx):
    """把 {主人会话}、{对话模型} 这类占位换成真值。"""
    if isinstance(value, str):
        for k, v in ctx.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [fill(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, ctx) for k, v in value.items()}
    return value


def pick_backend():
    return ask("base_url", "AstrBot 后台地址", "http://127.0.0.1:6185")


def upsert_provider(api, conf):
    pid = conf["id"]
    existing = provider_config(api, pid)
    if existing:
        merged = {**existing, **conf}
        r = api.call("PUT", f"/providers/{urllib.parse.quote(pid, safe='')}", {"config": merged})
    else:
        r = api.call("POST", "/providers", {"config": conf})
    if not api.ok(r):
        warn(f"保存「{pid}」失败：{api.msg(r)}")
        return False
    return True


def provider_config(api, pid):
    """读一个模型提供商的配置；没有就返回 None。"""
    r = api.call("GET", f"/providers/{urllib.parse.quote(pid, safe='')}")
    data = r.get("data") if api.ok(r) and isinstance(r.get("data"), dict) else {}
    data = data.get("provider", data)
    return data if isinstance(data, dict) and data.get("id") == pid else None


def test_provider(api, pid):
    r = api.call("POST", "/providers/test", {"provider_id": pid}, timeout=90)
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    status = str(data.get("status") or r.get("status") or "")
    if api.ok(r) and status not in ("error", "unavailable", "failed"):
        return True, ""
    return False, str(data.get("error") or data.get("message") or api.msg(r))[:200]


def extra_plugins():
    root = os.path.join(PACK, "plugins")
    zips = glob.glob(os.path.join(root, "*.zip"))
    dirs = [d for d in glob.glob(os.path.join(root, "*")) if os.path.isfile(os.path.join(d, "metadata.yaml"))]
    return sorted(zips + dirs)


def plugin_name(path):
    meta = os.path.join(path, "metadata.yaml")
    if os.path.isfile(meta):
        for line in open(meta, encoding="utf-8-sig"):
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def zip_folder(folder):
    import io
    import zipfile
    buf = io.BytesIO()
    top = os.path.basename(folder.rstrip("\\/"))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, f)
                zf.write(full, os.path.join(top, os.path.relpath(full, folder)))
    return buf.getvalue()


def multipart(field, filename, payload):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/zip\r\n\r\n").encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + payload + tail, f"multipart/form-data; boundary={boundary}"


def main():
    global ANSWERS
    if "--answers" in sys.argv:
        ANSWERS = json.load(open(sys.argv[sys.argv.index("--answers") + 1], encoding="utf-8-sig"))
    pack = json.load(open(os.path.join(PACK, "settings", "pack.json"), encoding="utf-8-sig"))
    say("大肥鱼配置包导入工具")
    say("新用户部署：先配置 AstrBot，QQ 登录由用户稍后在 NapCat 扫码完成。")

    # ---------- 连接后台 ----------
    api = Api(pick_backend())
    user = ask("username", "后台账号", "astrbot")
    password = ask("password", "后台密码（输入时不显示）", secret=True)
    if not api.login(user, password):
        sys.exit("登录失败：检查后台地址、账号、密码。")
    say("  已登录后台")

    owner = ask("owner_qq", "你自己的 QQ 号（大号，她会把它当主人）")
    if owner and not owner.isdigit():
        sys.exit("QQ 号只能是数字")
    memory = ask("memory", "云端记忆：2 硅基流动，0 跳过", "0", choices=["0", "2"])
    sf_key = ask("siliconflow_key", "硅基流动 key", secret=True) if memory == "2" else ""
    voice = ask("voice", "云端语音：3 豆包，0 跳过", "0", choices=["0", "3"])
    volc_app_id = volc_token = ""
    if voice == "3":
        volc_app_id = ask("volc_app_id", "豆包语音 APP ID")
        volc_token = ask("volc_access_token", "豆包语音 Access Token（不是火山方舟的 API Key；输入时不显示）", secret=True)

    r = api.call("GET", "/config-profiles/default")
    cfg = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    if not isinstance(cfg, dict):
        sys.exit(f"读不到配置：{api.msg(r)}")

    platforms = [p for p in cfg.get("platform", []) if p.get("type") == "aiocqhttp"]
    platforms.sort(key=lambda p: not p.get("enable", True))
    if not platforms:
        sys.exit("后台里还没有 QQ 机器人（OneBot v11），先按视频接好 NapCat 再来。")
    platform_id = platforms[0]["id"]
    chat_id = get_path(cfg, "agent_runner.config.model.provider_id") or ""
    if not chat_id:
        chats = [p for p in cfg.get("provider", []) if p.get("provider_type", "chat_completion") == "chat_completion" and p.get("enable", True)]
        chat_id = chats[0]["id"] if chats else ""
    if not chat_id:
        sys.exit("后台里还没有对话模型，先按视频接好 DeepSeek 再来。")
    session = f"{platform_id}:FriendMessage:{owner}" if owner else ""
    ctx = {"主人会话": session, "对话模型": chat_id, "唱歌后端": (pack.get("sing") or {}).get("backend") or "doubao"}
    say(f"  机器人：{platform_id}　对话模型：{chat_id}")
    results = []

    if memory == "0":
        pack["plugins"] = [p for p in pack["plugins"] if p["name"] != "astrbot_plugin_livingmemory"]
    if not owner:
        for item in pack["plugins"]:
            if item["name"] == "astrbot_plugin_proactive_chat":
                item["config"]["friend_settings.enable"] = False
                item["config"]["friend_settings.session_list"] = []

    # ---------- 对话模型：关思考，回得快 ----------
    step("调整对话模型")
    prov = provider_config(api, chat_id)
    if prov and "deepseek" in (str(prov.get("model", "")) + str(prov.get("provider_source_id", ""))).lower():
        extra = dict(prov.get("custom_extra_body") or {})
        extra.update(pack["chat_model"]["custom_extra_body"])
        prov["custom_extra_body"] = extra
        ok = api.ok(api.call("PUT", f"/providers/{urllib.parse.quote(chat_id, safe='')}", {"config": prov}))
        say("  已关闭深度思考，回得更快" if ok else "  ！没改成，不影响使用")
    else:
        say("  不是 DeepSeek 模型，跳过")

    # ---------- 人格 ----------
    step("写入人格「大肥鱼」")
    pid = pack["persona"]["id"]
    text = open(os.path.join(PACK, pack["persona"]["file"]), encoding="utf-8-sig").read().replace("{OWNER_QQ}", owner or "尚未指定（暂不启用管理员功能）")
    qid = urllib.parse.quote(pid, safe="")
    cur = api.call("GET", f"/personas/{qid}")
    if api.ok(cur) and isinstance(cur.get("data"), dict) and cur["data"].get("persona_id") == pid:
        body = {k: v for k, v in cur["data"].items() if k in ("begin_dialogs", "skills", "folder_id", "sort_order", "custom_error_message")}
        body.update({"system_prompt": text, "tools": None})
        r = api.call("PUT", f"/personas/{qid}", body)
    else:
        r = api.call("POST", "/personas", {"persona_id": pid, "system_prompt": text, "begin_dialogs": [], "tools": None})
    results.append(("人格", api.ok(r)))
    say("  完成" if api.ok(r) else f"  ！失败：{api.msg(r)}")

    # ---------- 插件 ----------
    step("从插件市场安装插件（第一次要下载，慢的话等一两分钟）")
    installed = {p.get("name") for p in (api.call("GET", "/plugins").get("data") or []) if isinstance(p, dict)}
    for p in pack["plugins"]:
        if p["name"] in installed:
            say(f"  {p['name']}：已装过")
            continue
        say(f"  {p['name']}：安装中…（{p['what']}）")
        r = api.call("POST", "/plugins/install/github",
                     {"install_method": "market", "market_plugin_id": p["market_id"]}, timeout=600)
        if not api.ok(r):
            warn(f"装不上：{api.msg(r)}")
            warn("插件是从插件市场的服务器下的，可能一时没响应。等几分钟再运行一次本工具；还装不上，就在后台「插件 → 插件市场」里搜这个插件手动装，装好再运行一次。")
    installed = {p.get("name") for p in (api.call("GET", "/plugins").get("data") or []) if isinstance(p, dict)}
    for p in pack["plugins"]:
        results.append((f"插件 {p['name']}", p["name"] in installed))

    # 额外插件（比如豆包语音）：plugins/ 里的 zip，或带 metadata.yaml 的插件文件夹
    for z in extra_plugins():
        base = z[:-4] if z.endswith(".zip") else z
        meta_path = base + ".json"
        meta = json.load(open(meta_path, encoding="utf-8-sig")) if os.path.exists(meta_path) else {}
        name = meta.get("name") or plugin_name(z) or os.path.basename(base)
        if meta.get("only_with_voice") and meta["only_with_voice"] != voice:
            continue
        if name in installed and not meta.get("always_reinstall"):
            say(f"  {name}：已装过")
        else:
            say(f"  {name}：从配置包安装…")
            payload = open(z, "rb").read() if z.endswith(".zip") else zip_folder(z)
            body, ctype = multipart("file", os.path.basename(base) + ".zip", payload)
            r = api.call("POST", "/plugins/install/upload", raw_body=body, content_type=ctype, timeout=600)
            if not api.ok(r):
                warn(f"装不上：{api.msg(r)}")
        if meta.get("config"):
            pack["plugins"].append({"name": name, "config": meta["config"], "what": meta.get("what", "")})
        installed = {p.get("name") for p in (api.call("GET", "/plugins").get("data") or []) if isinstance(p, dict)}
        results.append((f"插件 {name}", name in installed))

    step("改插件设置")
    changed = []
    for p in pack["plugins"]:
        if p["name"] not in installed or not p["config"]:
            continue
        r = api.call("GET", f"/plugins/config?plugin_id={urllib.parse.quote(p['name'])}")
        conf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
        if not isinstance(conf, dict):
            warn(f"{p['name']}：读不到设置")
            continue
        for k, v in p["config"].items():
            if get_path(conf, k) is None and "." in k and get_path(conf, k.rsplit(".", 1)[0]) is None:
                warn(f"{p['name']}：设置项 {k} 不存在，可能插件更新改名了，跳过")
                continue
            set_path(conf, k, fill(v, ctx))
        r = api.call("PUT", "/plugins/config", {"plugin_id": p["name"], "config": conf})
        say(f"  {p['name']}：{'完成' if api.ok(r) else '失败 ' + api.msg(r)}")
        if api.ok(r):
            changed.append(p["name"])

    for tool, perm in pack["tool_permissions"].items():
        r = api.call("PATCH", f"/tools/{urllib.parse.quote(tool, safe='')}/permission", {"permission": perm})
        if not api.ok(r):
            warn(f"工具 {tool} 权限没改成：{api.msg(r)}")
    say("  发空间、改签名只听主人的：已设置")

    # ---------- 表情包 ----------
    step("下载表情包（从表情包插件的资源广场下，几百张图，要一会儿）")
    if "meme_manager" in installed:
        ext = "/plugins/extensions/meme_manager"
        # 表情包从 GitHub 下，经插件里设的加速站；加速站时好时坏，下不动就换下一个再试
        cur = meme_accelerator(api)
        accels = ([cur] if cur and cur not in GH_ACCELERATORS else []) + GH_ACCELERATORS
        ai, gh_warned = 0, False
        if cur != accels[0]:
            meme_accelerator(api, accels[0])
        api.call("POST", f"{ext}/community/index/fetch", {}, timeout=120)
        have = meme_pack_ids(api, ext)
        for mp in pack["meme_packs"]:
            if mp["id"] in have:
                say(f"  {mp['id']}：已有")
                results.append((f"表情包 {mp['id']}", True))
                continue
            while True:
                r = api.call("POST", f"{ext}/community/install",
                             {"pack_id": mp["id"], "set_as_default": mp["default"]}, timeout=900)
                ok = api.ok(r)
                if ok or ai + 1 >= len(accels):
                    break
                ai += 1
                say(f"  {mp['id']}：没下下来（{api.msg(r)[:80]}），换个 GitHub 加速地址再试：{accels[ai] or '直连 GitHub'}")
                if meme_accelerator(api, accels[ai]) is None:
                    break
                api.call("POST", f"{ext}/community/index/fetch", {}, timeout=120)
            say(f"  {mp['id']}：{'完成' if ok else '失败 ' + api.msg(r)}")
            results.append((f"表情包 {mp['id']}", ok))
            if not ok and not gh_warned:
                gh_warned = True
                warn("几个加速地址都没下下来，多半是这会儿连不上 GitHub。过一阵再运行一次本工具；"
                     "或者在后台「插件 → meme_manager → 配置」里把 GitHub 加速地址换成你能用的，再运行一次。")
        # 默认规则写成「几套一起用」：meme_manager 的选择规则支持 pack_ids 列表，主套排第一
        use = [mp["id"] for mp in sorted(pack["meme_packs"], key=lambda m: not m.get("default"))
               if mp.get("use", True)]
        ok = bool(use) and meme_use_together(api, ext, use)
        if not ok:
            # 规则接口用不了（比如插件版本太老）：退回只设主套为默认
            main = next((mp["id"] for mp in pack["meme_packs"] if mp.get("default")), None)
            if main:
                ok = api.ok(api.call("POST", f"{ext}/packs/default", {"pack_id": main}))
                say(f"  只把 {main} 设成默认：{'完成' if ok else '失败'}")
        results.append(("表情包默认用的几套", ok))
    else:
        results.append(("表情包", False))

    # ---------- 记忆 ----------
    step("记忆")
    lm = "astrbot_plugin_livingmemory"
    mem_ok = False
    if memory == "0":
        say("  跳过云端记忆；以后填写硅基流动 Key 后重新配置即可。")
    else:
        m = pack["memory"]
        s = m["siliconflow"]
        conf = {"id": s["id"], "embedding_api_key": sf_key, "embedding_api_base": s["embedding_api_base"],
                "embedding_model": s["embedding_model"]}
        if conf and lm in installed:
            conf.update({"type": "openai_embedding", "provider": "openai", "provider_type": "embedding", "enable": True,
                         "embedding_dimensions": m["embedding_dimensions"], "embedding_dimensions_mode": "auto",
                         "timeout": 20, "proxy": ""})
            if upsert_provider(api, conf):
                good, why = test_provider(api, conf["id"])
                if not good:
                    warn(f"记忆模型测试没通过：{why}")
                r = api.call("GET", f"/plugins/config?plugin_id={lm}")
                lconf = (r.get("data") or {}).get("config")
                if isinstance(lconf, dict):
                    set_path(lconf, "provider_settings.embedding_provider_id", conf["id"])
                    mem_ok = api.ok(api.call("PUT", "/plugins/config", {"plugin_id": lm, "config": lconf})) and good
                    changed.append(lm)
    if lm in installed:
        api.call("PATCH", "/plugins/enabled", {"plugin_id": lm, "enabled": mem_ok})
        say("  记忆已打开" if mem_ok else "  记忆插件先关着（没配好记忆模型时开着会报错）")
    if memory != "0":
        results.append(("记忆", mem_ok))

    # ---------- 语音 ----------
    step("语音")
    v = pack["voice"]
    tts_id = ""
    if voice == "3":
        d = v["doubao"]
        if d["plugin"] not in installed:
            warn(f"豆包语音要的插件 {d['plugin']} 没装上，先跳过语音。")
        else:
            conf = {"id": d["id"], "type": "seed_audio_tts", "provider": "volcengine", "provider_type": "text_to_speech",
                    "enable": True, "app_id": volc_app_id, "access_key": volc_token, "credentials_file": "",
                    "api_base": d["api_base"], "resource_id": d["resource_id"], "model": d["model"],
                    "seed_audio_persona": d["persona"], "seed_audio_prompt_template": d["prompt_template"],
                    "seed_audio_format": d["format"], "seed_audio_sample_rate": d["sample_rate"],
                    "seed_audio_max_attempts": d["max_attempts"],
                    "seed_audio_whole_reply": d.get("whole_reply", True),
                    "seed_audio_reply_probability": v["trigger_probability"], "timeout": d["timeout"]}
            say("  试念一句「你好呀」，豆包要十几秒…")
            tts_id = d["id"] if upsert_provider(api, conf) else ""
    if tts_id:
        good, why = test_provider(api, tts_id)
        if good:
            say(f"  语音已打开，大约三条回复里有一条会发语音")
            if voice == "3":
                say("  豆包是整条回复合成一条语音，要等 15–35 秒才到")
        else:
            # 测不通就先关着：开着的话她每发一次语音都会报错
            warn(f"语音测试没通过：{why}")
            conf["enable"] = False
            upsert_provider(api, conf)
            tts_id = ""
            say("  语音先关着，处理好再运行一次本工具")
        results.append(("语音", good))
    elif voice != "0":
        results.append(("语音", False))
    else:
        say("  先不开")

    # ---------- 全局设置 ----------
    step("改全局设置（分段回复、认得主人、默认人格等）")
    r = api.call("GET", "/config-profiles/default")
    cfg = r["data"]["config"]
    for k, val in pack["base"].items():
        set_path(cfg, k, fill(val, ctx))
    ps = cfg.setdefault("platform_settings", {})
    if owner and ps.get("enable_id_white_list"):
        wl = ps.setdefault("id_whitelist", [])
        if session not in wl and owner not in wl:
            wl.append(session)
    admins = cfg.setdefault("admins_id", [])
    if owner and owner not in admins:
        admins.append(owner)
    tts = cfg.setdefault("provider_tts_settings", {})
    if tts_id and voice == "3" and v["doubao"].get("whole_reply", True):
        # 整条语音由豆包插件掷骰子、一次合成；AstrBot 自己的按段语音要关掉，不然会再按段转一遍
        tts.update({"enable": False, "provider_id": tts_id, "trigger_probability": v["trigger_probability"]})
    elif tts_id:
        tts.update({"enable": True, "provider_id": tts_id, "trigger_probability": v["trigger_probability"]})
    else:
        tts["enable"] = False
    r = api.call("PUT", "/config-profiles/default", cfg)
    results.append(("全局设置", api.ok(r)))
    say("  完成" if api.ok(r) else f"  ！失败：{api.msg(r)}")

    step("重新加载插件")
    for name in dict.fromkeys(changed):
        if name == lm and not mem_ok:
            continue
        api.call("POST", "/plugins/reload", {"plugin_id": name}, timeout=180)
    say("  完成")

    say("\n===== 结果 =====")
    for name, ok in results:
        say(f"  {'✔' if ok else '✘'} {name}")
    bad = [n for n, ok in results if not ok]
    if bad:
        say("\n打叉的可以按提示处理后，再运行一次本工具（已经装好的会自动跳过）。")
    say("\n配置结束。请用户在 NapCat 后台自行扫码登录 QQ，再从手机发消息验收。")
    global LAST_RESULTS
    LAST_RESULTS = results
    return not bad


def meme_payload(r, key):
    """meme_manager 的接口有时直接返回字段，有时包在 data 里。"""
    if key in r:
        return r[key]
    data = r.get("data")
    return data.get(key) if isinstance(data, dict) else None


def meme_pack_ids(api, ext):
    packs = meme_payload(api.call("GET", f"{ext}/packs"), "packs") or []
    return {str(p.get("pack_id") or p.get("id")) for p in packs if isinstance(p, dict)}


def meme_accelerator(api, url=None):
    """不给 url：读表情包插件现在的 GitHub 加速地址（读不到返回 None）。给 url：改成它，成功返回 url，失败返回 None。"""
    r = api.call("GET", "/plugins/config?plugin_id=meme_manager")
    conf = (r.get("data") or {}).get("config") if isinstance(r.get("data"), dict) else None
    if not isinstance(conf, dict):
        return None
    if url is None:
        return str(get_path(conf, "community.github_accelerator_url") or "")
    set_path(conf, "community.github_accelerator_url", url)
    return url if api.ok(api.call("PUT", "/plugins/config", {"plugin_id": "meme_manager", "config": conf})) else None


def meme_use_together(api, ext, wanted):
    """把 meme_manager 的 default 选择规则设成 wanted 这几套一起用（已经是了就不动）。"""
    have = meme_pack_ids(api, ext)
    ids = [p for p in wanted if p in have]
    missing = [p for p in wanted if p not in have]
    if missing:
        warn(f"这几套表情包没装上，先不算进去：{'、'.join(missing)}")
    if not ids:
        return False
    r = api.call("GET", f"{ext}/settings/rules")
    rules = [x for x in (meme_payload(r, "rules") or []) if isinstance(x, dict)]
    if not api.ok(r):
        warn(f"读不到表情包规则：{api.msg(r)}")
        return False
    cur = next((x for x in reversed(rules) if x.get("scope") == "default"), {})
    cur_ids = cur.get("pack_ids") or ([cur["pack_id"]] if cur.get("pack_id") else [])
    if cur_ids == ids:
        say(f"  默认已经是这 {len(ids)} 套：{'、'.join(ids)}")
        return True
    keep = [x for x in rules if x.get("scope") != "default"]
    rule = {"id": cur.get("id") or "default", "scope": "default", "pack_id": ids[0], "pack_ids": ids}
    r = api.call("POST", f"{ext}/settings/rules", {"rules": keep + [rule]})
    if not api.ok(r):
        warn(f"表情包规则没改成：{api.msg(r)}")
        return False
    say(f"  默认改成用这 {len(ids)} 套：{'、'.join(ids)}")
    return True


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        main()
    except KeyboardInterrupt:
        say("\n已取消")
