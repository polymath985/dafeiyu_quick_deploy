"""大肥鱼回复防串台：模型偶尔会把内部标签当成回复吐出来（10-25 正式实例：「才没有 我在研究今天午饭吃什么
这算正事 <system_reminder>User ID: …, Nickname: …</｜DSML｜parameter> </｜DSML｜invoke> …」）。

三处一起清（规则见 clean.py）：
1. on_llm_request：发给模型的历史里，之前漏进去的助手回复先洗干净，免得她照着学；
2. on_agent_done：这一轮她的回复存进聊天记录前洗干净；
3. on_decorating_result：发出去之前最后洗一遍（排在表情包、语音、分段前面）。
"""

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from .clean import clean, dirty


def _clean_content(content):
    """content 可能是字符串、dict 列表（OpenAI 格式）或 TextPart 列表。返回 (新 content, 改没改)。"""
    if isinstance(content, str):
        new = clean(content)
        return new, new != content
    changed = False
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and dirty(part["text"]):
                part["text"] = clean(part["text"])
                changed = True
            elif isinstance(getattr(part, "text", None), str) and dirty(part.text):
                part.text = clean(part.text)
                changed = True
    return content, changed


class DafeiyuClean(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.on_llm_request(priority=100000)
    async def clean_history(self, event: AstrMessageEvent, req):
        n = 0
        for msg in getattr(req, "contexts", None) or []:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                msg["content"], changed = _clean_content(msg.get("content"))
                n += changed
        if n:
            logger.info(f"[dafeiyu_clean] 历史里 {n} 条回复带内部标签，发给模型前洗掉了")

    @filter.on_llm_response(priority=100000)
    async def clean_response(self, event: AstrMessageEvent, resp):
        text = getattr(resp, "completion_text", None)
        if isinstance(text, str) and dirty(text):
            try:
                resp.completion_text = clean(text)
            except Exception:
                pass

    @filter.on_agent_done(priority=100000)
    async def clean_saved(self, event: AstrMessageEvent, run_context, llm_response):
        messages = getattr(run_context, "messages", None) or []
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "assistant":
                content, changed = _clean_content(msg.content)
                if changed:
                    msg.content = content
                    logger.info("[dafeiyu_clean] 这条回复带内部标签，存聊天记录前洗掉了")
                break

    @filter.on_decorating_result(priority=100000)
    async def clean_outgoing(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return
        new_chain, hit = [], False
        for comp in result.chain:
            if isinstance(comp, Plain) and dirty(comp.text):
                hit = True
                comp.text = clean(comp.text)
                if not comp.text:
                    continue
            new_chain.append(comp)
        if hit:
            result.chain = new_chain
            logger.warning("[dafeiyu_clean] 回复里漏了内部标签（system_reminder / DSML），发出去前删掉了")
