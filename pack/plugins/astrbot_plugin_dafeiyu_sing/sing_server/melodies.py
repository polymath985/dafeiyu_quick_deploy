"""三首公版儿歌的谱子，以及「歌词 → SoulX-Singer 谱面 JSON」的转换。

每首歌写成若干「歌词行」：一行对应一句歌词，里面是 (MIDI 音高, 拍数)。
歌词字数和音符数对不上时自动凑：字多了就把长音劈开，字少了就让最后一个字拖长。
"""
import re
import statistics

# repeat：这一行歌词连唱几遍（两只老虎每句唱两遍）
MELODIES = {
    "小星星": {
        "bpm": 110,
        "lines": [
            {"notes": [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2)]},
            {"notes": [(65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)]},
            {"notes": [(60, 1), (60, 1), (67, 1), (67, 1), (69, 1), (69, 1), (67, 2)]},
            {"notes": [(65, 1), (65, 1), (64, 1), (64, 1), (62, 1), (62, 1), (60, 2)]},
        ],
        "default": ["一闪一闪亮晶晶", "满天都是小星星", "一闪一闪亮晶晶", "满天都是小星星"],
    },
    "两只老虎": {
        "bpm": 120,
        "lines": [
            {"notes": [(60, 1), (62, 1), (64, 1), (60, 1)], "repeat": 2},
            {"notes": [(64, 1), (65, 1), (67, 2)], "repeat": 2},
            {"notes": [(67, 0.5), (69, 0.5), (67, 0.5), (65, 0.5), (64, 1), (60, 1)], "repeat": 2},
            {"notes": [(60, 1), (55, 1), (60, 2)], "repeat": 2},
        ],
        "default": ["两只老虎", "跑得快", "一只没有耳朵", "真奇怪"],
    },
    "生日快乐": {
        "bpm": 100,
        "lines": [
            {"notes": [(55, 0.75), (55, 0.25), (57, 1), (55, 1), (60, 1), (59, 2)]},
            {"notes": [(55, 0.75), (55, 0.25), (57, 1), (55, 1), (62, 1), (60, 2)]},
            {"notes": [(55, 0.75), (55, 0.25), (67, 1), (64, 1), (60, 1), (59, 1), (57, 2)]},
            {"notes": [(65, 0.75), (65, 0.25), (64, 1), (60, 1), (62, 1), (60, 2)]},
        ],
        "default": ["祝你生日快乐", "祝你生日快乐", "祝你生日快乐呀", "祝你生日快乐"],
    },
}

ALIASES = {
    "twinkle": "小星星", "一闪一闪亮晶晶": "小星星", "星星": "小星星",
    "老虎": "两只老虎", "两只老虎跑得快": "两只老虎",
    "生日歌": "生日快乐", "生日": "生日快乐", "祝你生日快乐": "生日快乐", "birthday": "生日快乐",
}

_DIGITS = dict(zip("0123456789", "零一二三四五六七八九"))
_SPLIT = re.compile(r"[\n/／|｜，,。.、；;！!？?\s]+")
_HAN = re.compile(r"[一-鿿]")

LEAD_SP = 0.25   # 每段开头的空白（秒）
BREATH = 0.2     # 每句末尾换气（秒）
MAX_SEGMENT = 10.0  # 每段最长秒数，一段一次推理


def resolve_melody(name):
    name = (name or "").strip()
    if name in MELODIES:
        return name
    for k, v in ALIASES.items():
        if k in name.lower():
            return v
    for k in MELODIES:
        if k in name:
            return k
    return "小星星"


def lyric_lines(melody, lyrics):
    """把模型写的歌词切成和谱子行数一样多的句子，每句只留汉字。"""
    m = MELODIES[melody]
    want = [len(line["notes"]) for line in m["lines"]]
    text = "".join(_DIGITS.get(c, c) for c in (lyrics or ""))
    parts = ["".join(_HAN.findall(p)) for p in _SPLIT.split(text)]
    parts = [p for p in parts if p]
    if len(parts) != len(want):
        # 句数对不上：把所有字连起来按每句应有的字数重新切
        chars = "".join(parts)
        parts, i = [], 0
        for n in want:
            parts.append(chars[i:i + n])
            i += n
    return [p if p else m["default"][k] for k, p in enumerate(parts[:len(want)])]


def fit(chars, notes):
    """一个字配一个音。字多了劈开最长的音，字少了最后一个字拖长。"""
    notes = list(notes)
    while len(notes) < len(chars):
        i = max(range(len(notes)), key=lambda k: notes[k][1])
        p, b = notes[i]
        notes[i:i + 1] = [(p, b / 2), (p, b / 2)]
    if len(notes) > len(chars):
        tail = notes[len(chars) - 1:]
        notes = notes[:len(chars) - 1] + [(tail[0][0], sum(b for _, b in tail))]
    return list(zip(chars, notes))


def to_phonemes(line, phoneset=None):
    from pypinyin import Style, lazy_pinyin  # 只有 SoulX 要拼音；豆包唱歌在 AstrBot 里跑，不装 pypinyin 也行

    out = []
    for py in lazy_pinyin(line, style=Style.TONE3, neutral_tone_with_five=True, tone_sandhi=True):
        py = py.replace("v", "u:")
        if not py[-1].isdigit():
            py += "5"
        ph = "zh_" + py
        if phoneset is not None and ph not in phoneset:
            # 这个声调不在音素表里，换同音节的别的声调
            cands = [f"zh_{py[:-1]}{t}" for t in "51234"]
            ph = next((c for c in cands if c in phoneset), "<SP>")
        out.append(ph)
    return out


def build(melody, lyrics, phoneset=None, prompt_median=None):
    """返回 (SoulX 谱面 list, 移调半音数, 实际唱的歌词行)。"""
    melody = resolve_melody(melody)
    m = MELODIES[melody]
    beat = 60 / m["bpm"]
    lines = lyric_lines(melody, lyrics)

    sung = []  # 每个元素是一句：[(字, 音素, 音高, 秒), ...]
    for spec, text in zip(m["lines"], lines):
        phs = to_phonemes(text, phoneset)
        one = [(c, ph, p, b * beat) for (c, (p, b)), ph in zip(fit(text, spec["notes"]), phs)]
        sung.extend([one] * spec.get("repeat", 1))

    segments, cur, t = [], None, 0.0
    for one in sung:
        line_len = sum(d for *_, d in one)
        if cur is None or cur["len"] + line_len > MAX_SEGMENT:
            if cur:
                segments.append(cur)
                t += cur["len"]
            cur = {"start": t, "len": LEAD_SP, "tok": [("<SP>", "<SP>", 0, LEAD_SP, 1)]}
        breath = min(BREATH, one[-1][3] * 0.3)
        for k, (c, ph, p, d) in enumerate(one):
            if k == len(one) - 1:
                d -= breath
            cur["tok"].append((c, ph, p, d, 2))
        cur["tok"].append(("<SP>", "<SP>", 0, breath, 1))
        cur["len"] += line_len
    segments.append(cur)

    metas = []
    for i, seg in enumerate(segments):
        start = round(seg["start"] * 1000)
        metas.append({
            "index": str(i),
            "language": "Mandarin",
            "time": [start, start + round(seg["len"] * 1000)],
            "duration": " ".join(f"{d:.3f}" for *_, d, _ in seg["tok"]),
            "text": " ".join(c for c, *_ in seg["tok"]),
            "phoneme": " ".join(ph for _, ph, *_ in seg["tok"]),
            "note_pitch": " ".join(str(p) for _, _, p, _, _ in seg["tok"]),
            "note_type": " ".join(str(ty) for *_, ty in seg["tok"]),
        })

    shift = 0
    if prompt_median is not None:
        song = [p for one in sung for _, _, p, _ in one]
        shift = round(prompt_median - statistics.median(song))
    return metas, shift, lines
