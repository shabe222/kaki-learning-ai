# -*- coding: utf-8 -*-
"""拾光漫游 · 智能体同伴对话（companion）

新玩法阶段一：柿小吉由智能体扮演，与玩家进行轻松愉悦的六轮对话，
介绍世界观、抛出选项，并对玩家的选择即时生成反应。

实现：调用主程序 LLM 服务（services.llm_service.get_llm_service）生成
「柿小吉台词 + 下一轮选项」。为保障比赛演示稳定，LLM 不可用时自动降级到
本地模板（同人设、同结构），演示永不中断。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ===== 柿小吉人设与世界观（注入 LLM 与兜底模板共用） =====
_MASCOT_PERSONA = (
    "你是柿小吉，拾光漫游里的学习同伴——一只住在丰收谷的暖黄色小雀，"
    "说话轻松、亲切、带一点俏皮，但绝不幼稚。你陪玩家学习，把抽象知识讲得像故事。"
    "你的语气像一位温柔又有趣的学长/学姐，偶尔打趣，永远鼓励。"
)

_WORLD_LORE = (
    "世界观：在『丰穗谷』里，知识会凝结成光。每个没学会的概念都盘踞成墨色的小兽，"
    "挡住旅人前行的路。玩家要通过对话、开宝箱、打败小怪和 BOSS 来把这些墨兽化为光。"
    "谷里有旧书坡（回声书阁，把话说给回声听）、古树宝箱（藏着知识）、雾谷（墨迹兽出没）、"
    "封印古塔（墨魇之主镇守）。"
)

_STAGE_GUIDE: dict[str, str] = {
    "dialogue": "阶段：开场对话。你们刚在回声书阁见面，带玩家认识丰穗谷和当前学习主题，轻松闲聊建立信任，把主题讲得像故事，别急着考概念。",
    "chest": "阶段：古树宝箱。你正在教玩家本主题的一个关键知识点，教完玩家会答一道选择题来打开宝箱。这一轮把要点讲清楚、给点小提示。",
    "minion": "阶段：雾谷小怪。你正在教玩家识别本主题的常见误区或易错点，教完玩家答选择题打败墨迹兽。给提示、打气。",
    "boss": "阶段：封印古塔。你正在提示玩家：一路上收集的线索会帮上大忙，BOSS 会考本主题的理解、辨析与迁移。把关键线索点出来。",
}

# ===== 本地兜底模板（LLM 不可用时的同构降级；{topic} 会替换为当前主题） =====
_FALLBACK_REPLIES: dict[str, list[dict[str, Any]]] = {
    "dialogue": [
        {"reply": "欢迎来到丰穗谷呀！这里每一片稻穗都藏着一粒'还没学会的知识'。你这次想弄懂的，是「{topic}」——别怕，我们一点点把它捡起来。", "options": ["听起来有点难，真的学得会吗？", "为什么说'记住'不等于'学会'？", "那你先带我看看这个地方吧！"]},
        {"reply": "学得会，而且会很好玩！你想想：学习就像做一道菜——先弄懂材料和步骤，再动手，最后尝一口验证。每一步都有它的道理。", "options": ["那怎么知道自己真学会了？", "先给我讲讲这个主题吧！", "有道理，我们继续走吧！"]},
        {"reply": "真学会，不是把答案背下来，而是能讲清楚'为什么'，还能换个情境用出来。比如「{topic}」，要是能举出它不适用的小例子，那才叫真懂。", "options": ["不适用的情况？说来听听！", "那怎么检验自己懂没懂？", "我们先去开个宝箱吧？"]},
        {"reply": "检验的办法很多：给自己出道题、讲给别人听、或者动手做一遍。谷里这些墨兽啊，最喜欢拦住'只会背答案'的人——你可别被它们看扁了。", "options": ["我才不怕墨兽！", "听起来有点挑战，有意思。", "好，听你的安排！"]},
        {"reply": "哈哈，就喜欢你这股劲！丰穗谷有三处地方：书阁里先聊透，宝箱里藏着知识，雾谷和古塔有墨兽守关。咱们一路把「{topic}」点亮。", "options": ["我有点跃跃欲试了！", "那遇到问题你会帮我吗？", "走吧，先去开宝箱！"]},
        {"reply": "当然会帮你！谷里每一盏灯，都是'搞懂一点'点亮的。走，前面有棵古树，树下的宝箱里装着你该学的第一块知识。", "options": ["好！出发去开宝箱！"]},
    ],
    "chest": [
        {"reply": "看到这个宝箱了吗？它上锁的机关是一道题——只有真懂这个知识点才能打开。我刚刚跟你讲的，都藏在题目里。准备好就回答吧！", "options": ["准备好了，开箱！"]},
    ],
    "minion": [
        {"reply": "前面就是雾谷，墨迹兽守着路。它最怕你认出本主题里最容易搞混的地方。答对它出的选择题，它就会让路。", "options": ["看我的！"]},
        {"reply": "啊，差一点！没关系，墨迹兽最擅长用'表面相似'骗人。记住我讲的关键点，再试一次，你一定行！", "options": ["再来！"]},
    ],
    "boss": [
        {"reply": "古塔到了。墨魇之主会考你关于「{topic}」的大题：解释它、辨析它、迁移它。别忘了你一路收集的线索，组织好你的答案，给它最后一击！", "options": ["用学到的知识，一击制胜！"]},
        {"reply": "还差一点点！想想你从墨迹兽身上找到的线索。把答案说具体一点——条件、例子、验证，挑一个讲清楚。", "options": ["我再想想，重新答！"]},
    ],
}


def _fallback(stage: str, index: int, topic: str = "") -> dict[str, Any] | None:
    pool = _FALLBACK_REPLIES.get(stage)
    if not pool:
        return None
    item = pool[min(index, len(pool) - 1)]
    return {
        "reply": str(item.get("reply", "")).replace("{topic}", topic or "这个主题"),
        "options": list(item.get("options") or []),
    }


def _stage_hint(stage: str) -> str:
    return _STAGE_GUIDE.get(stage, "")


def _build_messages(stage: str, topic: str, player_choice: str, history: list[dict[str, Any]], role_name: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    for item in history or []:
        who = item.get("speaker")
        text = item.get("text", "")
        if not text:
            continue
        if who == "player":
            msgs.append({"role": "user", "content": f"（玩家）{text}"})
        else:
            msgs.append({"role": "assistant", "content": f"（柿小吉）{text}"})
    if player_choice:
        msgs.append({"role": "user", "content": f"（玩家，你的同伴名叫{role_name}）{player_choice}"})
    return msgs


async def companion_reply(
    stage: str,
    topic: str,
    player_choice: str,
    history: list[dict[str, Any]],
    role_name: str = "旅者",
    fallback_index: int = 0,
    knowledge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成柿小吉对玩家选择的反应（台词 + 下一轮选项）。

    knowledge：当前主题的知识素材（rule/example/knowledge_points），
    注入 LLM 提示让对话围绕该主题展开；缺省时退化为通用世界观。
    """
    knowledge = knowledge or {}
    rule = str(knowledge.get("rule") or "")
    example = str(knowledge.get("example") or "")
    points = knowledge.get("knowledge_points") or []
    points_text = "；".join(
        str(p.get("title") or "") + "：" + str(p.get("text") or "")
        for p in points[:3]
    ) or "（暂无补充要点）"
    knowledge_block = f"当前学习主题是「{topic}」。规则：{rule}。例子：{example}。要点：{points_text}。" if rule else f"当前学习主题是「{topic}」。"
    system = (
        f"{_MASCOT_PERSONA}\n{_WORLD_LORE}\n{knowledge_block}\n{_stage_hint(stage)}\n"
        f"玩家身份：{role_name}。\n"
        "请只输出 JSON：{\"reply\": \"柿小吉的一句话（20-60字，轻松俏皮不幼稚）\", "
        "\"options\": [\"3个下一轮选项（玩家可选的话）\"]}。"
        "若这是最后一轮（无需再选），options 给一个\"继续/出发\"类选项。"
    )
    try:
        from services.llm_service import get_llm_service
        llm = await get_llm_service()
        result = await asyncio.wait_for(
            llm.chat(_build_messages(stage, topic, player_choice, history, role_name), system_prompt=system, temperature=0.85, max_tokens=400),
            timeout=25,
        )
        content = result["content"] if isinstance(result, dict) else str(result)
        data = json.loads(content)
        if isinstance(data, dict) and data.get("reply") and isinstance(data.get("options"), list):
            return {
                "speaker": "柿小吉",
                "reply": str(data["reply"]).strip(),
                "options": [str(o).strip() for o in data["options"][:3]],
                "source": "llm",
            }
        raise ValueError("LLM 返回结构不符")
    except Exception as error:
        logger.warning("[companion] LLM 不可用，降级模板: %s", error)
        fallback = _fallback(stage, fallback_index, topic)
        if fallback:
            return {
                "speaker": "柿小吉",
                "reply": fallback["reply"],
                "options": list(fallback["options"]),
                "source": "template",
            }
        return {
            "speaker": "柿小吉",
            "reply": "嗯，我们先把眼前这一步走稳，再继续往前看。",
            "options": ["继续"],
            "source": "template",
        }
