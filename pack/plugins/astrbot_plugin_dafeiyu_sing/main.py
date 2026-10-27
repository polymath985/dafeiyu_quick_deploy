import asyncio
import json
import os
import tempfile
import time

import aiohttp

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from . import doubao_sing


def _read_env_file(path):
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                out[k.strip()] = v.strip()
    return out


class DafeiyuSing(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.singing = False
        self.tasks = set()

    @property
    def server(self) -> str:
        return (self.conf.get("server_url") or "http://127.0.0.1:18620").rstrip("/")

    @property
    def backend(self) -> str:
        return self.conf.get("backend") or "doubao"

    def _doubao_creds(self):
        """先看插件设置里填没填；没填就借「豆包 Seed Audio」语音提供商那套（第 4 章申请的同一套）。"""
        app_id = str(self.conf.get("doubao_app_id") or "").strip()
        token = str(self.conf.get("doubao_access_token") or "").strip()
        if app_id and token:
            return app_id, token
        for p in self.context.get_all_tts_providers():
            a, t = getattr(p, "app_id", ""), getattr(p, "access_key", "")
            if getattr(p, "resource_id", "") == doubao_sing.RESOURCE_ID and a and t:
                return a, t
        try:
            providers = self.context.get_config().get("provider", [])
        except Exception:
            providers = []
        for c in providers:
            if not isinstance(c, dict) or c.get("type") != "seed_audio_tts":
                continue
            a, t = str(c.get("app_id") or ""), str(c.get("access_key") or "")
            cred = str(c.get("credentials_file") or "")
            if cred and not (a and t) and os.path.isfile(cred):
                env = _read_env_file(cred)
                a, t = a or env.get("VOLC_SPEECH_APP_ID", ""), t or env.get("VOLC_SPEECH_ACCESS_TOKEN", "")
            if a and t:
                return a, t
        return None

    async def _ready(self) -> bool:
        if self.backend == "doubao":
            if self._doubao_creds():
                return True
            logger.warning("[dafeiyu_sing] 找不到豆包语音的 APP ID 和 Access Token：导入工具语音选 3，或在插件设置里填")
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.server}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return self.backend in (await r.json()).get("backends", [])
        except Exception as e:
            logger.warning(f"[dafeiyu_sing] 唱歌服务连不上: {e!r}")
            return False

    @filter.llm_tool(name="sing_song")
    async def sing_song(self, event: AstrMessageEvent, melody: str, lyrics: str):
        '''对方让你唱歌（唱首歌、来一首、唱个生日歌）时调用；你自己想唱的时候（比如主人过生日）也可以调用。说了要唱就一定调用，别光说不唱。你现编歌词，套进一首儿歌的调子里，唱好的语音会在半分钟到一分钟后自动发过去。

        歌词每句字数要和曲子对上，句子之间用 / 隔开，只用汉字，不加标点和表情：
        - 小星星：4 句，每句 7 个字
        - 两只老虎：4 句，字数依次是 4、3、6、3（每句会唱两遍）
        - 生日快乐：4 句，字数依次是 6、6、7、6（过生日就选它）
        歌词写你自己的口吻，跟对方、跟刚才聊的事有关，可爱好玩一点。
        调用后你只回一两句很短的话，比如「等我开嗓」，别把歌词再发一遍。

        Args:
            melody(string): 曲子，只能是「小星星」「两只老虎」「生日快乐」之一
            lyrics(string): 新写的歌词，句子之间用 / 隔开
        '''
        if self.singing:
            return "上一首还在唱，告诉对方等这首唱完再点歌。"
        if not await self._ready():
            return "唱歌服务没开，你今天唱不了。用你的口吻跟对方说嗓子哑了，下次再唱，别提服务和技术细节。"

        self.singing = True
        task = asyncio.create_task(self._sing_and_send(event, melody, lyrics))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return "已经开始唱了，语音大概半分钟到一分钟后自动发给对方。你现在只回一两句很短的话说你在开嗓，别把歌词再发一遍。"

    def _log(self, rec: dict) -> None:
        try:
            path = os.path.join(get_astrbot_temp_path(), "dafeiyu_sing_calls.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def _sing_doubao(self, event: AstrMessageEvent, melody: str, lyrics: str):
        res = await doubao_sing.sing(melody, lyrics, self._doubao_creds(), get_astrbot_temp_path(),
                                     tries=int(self.conf.get("doubao_tries") or 2), log=logger.info)
        logger.info(f"[dafeiyu_sing] doubao {res['melody']} {'/'.join(res['lyrics'])} {res['seconds']}s audio, "
                    f"gen {res['gen_time']}s, lyrics_match={res['lyrics_match']}, takes={len(res['takes'])}, "
                    f"{res['cost_yuan']} yuan -> {res['file']}")
        self._log({"ts": time.time(), "backend": "doubao", "melody": res["melody"], "lyrics": res["lyrics"],
                   "seconds": res["seconds"], "gen_time": res["gen_time"], "takes": res["takes"],
                   "cost_yuan": res["cost_yuan"]})
        return res["path"]

    async def _sing_and_send(self, event: AstrMessageEvent, melody: str, lyrics: str):
        try:
            if self.backend == "doubao":
                path = await self._sing_doubao(event, melody, lyrics)
                await event.send(MessageChain([Comp.Record.fromFileSystem(path)]))
                return
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.server}/sing", json={"melody": melody, "lyrics": lyrics, "backend": self.backend},
                                  timeout=aiohttp.ClientTimeout(total=300)) as r:
                    res = await r.json()
                if not res.get("ok"):
                    raise RuntimeError(res.get("error"))
                path = res["path"]
                if not os.path.isfile(path):
                    # 唱歌服务在另一台电脑上：把 wav 拉回来
                    async with s.get(f"{self.server}/out/{res['file']}") as r:
                        data = await r.read()
                    path = os.path.join(tempfile.gettempdir(), res["file"])
                    with open(path, "wb") as f:
                        f.write(data)
            extra = (f", in_tune={res['in_tune']}, takes={len(res['takes'])}, {res['cost_yuan']} yuan"
                     if res.get("backend") == "doubao" else "")
            logger.info(f"[dafeiyu_sing] {res.get('backend', 'soulx')} {res['melody']} {'/'.join(res['lyrics'])} "
                        f"{res['seconds']}s audio, gen {res['gen_time']}s{extra} -> {res['file']}")
            await event.send(MessageChain([Comp.Record.fromFileSystem(path)]))
        except Exception as e:
            logger.error(f"[dafeiyu_sing] 唱歌失败: {e!r}")
            await event.send(MessageChain([Comp.Plain("唱劈了")]))
        finally:
            self.singing = False

    async def terminate(self):
        for t in list(self.tasks):
            t.cancel()
