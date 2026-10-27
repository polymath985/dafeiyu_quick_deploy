"""插件里直接调豆包 Seed Audio 1.0 唱歌，不用另开唱歌服务。

凭证和第 4 章发语音用的是同一套（豆包语音控制台旧版应用的 APP ID + Access Token）。
豆包不会照原曲的调子唱，唱的是它自己编的小调；所以这里不打音准分，只核对歌词：
字幕里的字和歌词对上八成就发，对不上再抽一次，还对不上就发对得最好的那条。
按生成的音频时长计费，1 元/分钟；一首十几到二十秒，一次两三毛。
"""
import base64
import difflib
import json
import os
import re
import time
import uuid
import wave

import aiohttp

try:
    from .sing_server import melodies
except ImportError:  # 单独跑测试时
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sing_server"))
    import melodies

URL = "https://openspeech.bytedance.com/api/v3/tts/create"
RESOURCE_ID = "seed-audio-1.0"
SAMPLE_RATE = 24000
PRICE_PER_MIN = 1.0
PERSONA = "一个憨萌软糯的小女生，声音甜甜的、有点奶，嘴馋爱撒娇，带一点小傲娇"
TUNES = {
    "小星星": "《小星星》（一闪一闪亮晶晶，满天都是小星星）",
    "两只老虎": "《两只老虎》（两只老虎，两只老虎，跑得快，跑得快）",
    "生日快乐": "《生日快乐歌》（祝你生日快乐）",
}
LYRICS_MIN = 0.8
TAIL_SILENCE = 0.3  # 末尾补 0.3 秒静音，免得最后一个字被截掉
_HAN = re.compile(r"[一-鿿]")


def build_prompt(melody, lines, persona=PERSONA):
    m = melodies.MELODIES[melody]
    sung, beats = [], 0.0
    for spec, line in zip(m["lines"], lines):
        sung += [line] * spec.get("repeat", 1)
        beats += sum(b for _, b in spec["notes"]) * spec.get("repeat", 1)
    seconds = round(beats * 60 / m["bpm"])
    # 不写速度时豆包会唱得很赶（28 个字 10 秒），所以把整首该唱多久写进去
    return (f"{persona}。她像幼儿园老师教小朋友那样，慢慢地清唱儿歌{TUNES[melody]}，"
            f"旋律和原曲一模一样，只是把歌词换成了她自己编的。一个字一拍，音唱准、唱稳，"
            f"每句最后一个字拖长，整首大约唱{seconds}秒。不说话、不加别的词，直接开唱："
            f"“{'，'.join(sung)}。”"), "".join(sung)


def lyric_match(heard, expected):
    a, b = "".join(_HAN.findall(heard or "")), "".join(_HAN.findall(expected))
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 2) if b else 0.0


async def _generate(creds, prompt, timeout):
    body = {"model": RESOURCE_ID, "text_prompt": prompt,
            "audio_config": {"format": "pcm", "sample_rate": SAMPLE_RATE, "enable_subtitle": True}}
    headers = {"X-Api-App-Id": creds[0], "X-Api-Access-Key": creds[1], "X-Api-Resource-Id": RESOURCE_ID,
               "X-Api-Request-Id": str(uuid.uuid4()), "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as s:
        async with s.post(URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers,
                          timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            raw = await r.text()
    try:
        j = json.loads(raw)
    except Exception:
        raise RuntimeError(f"豆包返回的不是 JSON（HTTP {r.status}）：{raw[:200]}")
    if not j.get("audio"):
        raise RuntimeError(f"豆包没出音频：code={j.get('code')} message={j.get('message')}")
    sub = j.get("subtitle") or {}
    heard = sub.get("text") or "".join(x.get("text", "") for x in sub.get("sentences", []))
    return base64.b64decode(j["audio"]), float(j.get("original_duration") or 0), heard


def _channels(pcm, dur):
    # 豆包回的 pcm 是 16 位双声道（10-25 实测）；按计费时长反推声道数，接口改了也不会把歌拉长一倍
    if dur <= 0:
        return 2
    return min(2, max(1, round(len(pcm) / 2 / SAMPLE_RATE / dur)))


def _write_wav(path, pcm, channels):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm + b"\0\0" * channels * int(TAIL_SILENCE * SAMPLE_RATE))


async def sing(melody, lyrics, creds, out_dir, tries=2, timeout=120, persona=PERSONA, log=print):
    melody = melodies.resolve_melody(melody)
    lines = melodies.lyric_lines(melody, lyrics)
    prompt, expected = build_prompt(melody, lines, persona)
    os.makedirs(out_dir, exist_ok=True)
    stem = f"sing-{time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    takes, billed, t_all, best = [], 0.0, time.time(), None
    for i in range(1, max(1, tries) + 1):
        t0 = time.time()
        try:
            pcm, dur, heard = await _generate(creds, prompt, timeout)
        except Exception as e:
            log(f"[dafeiyu_sing] 豆包第 {i} 次没成：{e}")
            takes.append({"take": i, "error": str(e)[:200]})
            continue
        billed += dur
        take = {"take": i, "billed": round(dur, 1), "gen_time": round(time.time() - t0, 1),
                "lyrics_match": lyric_match(heard, expected), "heard": heard}
        takes.append(take)
        log(f"[dafeiyu_sing] 豆包第 {i} 次：{take}")
        if best is None or take["lyrics_match"] > best[0]["lyrics_match"]:
            best = (take, pcm, _channels(pcm, dur))
        if take["lyrics_match"] >= LYRICS_MIN:
            break
    if best is None:
        raise RuntimeError(f"豆包 {len(takes)} 次都没出音频：{takes[-1].get('error')}")
    take, pcm, channels = best
    path = os.path.join(out_dir, f"{stem}.wav")
    _write_wav(path, pcm, channels)
    return {"ok": True, "path": path, "file": os.path.basename(path),
            "seconds": round(len(pcm) / 2 / channels / SAMPLE_RATE + TAIL_SILENCE, 1),
            "gen_time": round(time.time() - t_all, 1), "melody": melody, "lyrics": lines,
            "backend": "doubao", "lyrics_match": take["lyrics_match"], "takes": takes,
            "billed_seconds": round(billed, 1), "cost_yuan": round(billed / 60 * PRICE_PER_MIN, 2)}
