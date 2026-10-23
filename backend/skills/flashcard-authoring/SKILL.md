---
name: flashcard-authoring
description: 按检索练习（retrieval practice）与最小信息原则制作记忆卡组——一卡一判断，正面只留线索，负面禁止漏答，附间隔重复调度契约。
---

# 记忆卡制作法（检索练习 / 最小信息原则）

## 卡片规则

1. 一张卡只考一个可独立判断的点；两句话能说清的事拆成两张卡。
2. 正面是问题或补全线索，不含答案片段；禁止「是的/不是」类可猜测答案的问法。
3. 背面先给结论，再用一句依据支撑；依据必须来自源材料，不得引入新材料。
4. 卡片分三种 kind：concept（规则/关系）、fact（定义/数值）、application（给情境答结果）。
   application 卡的正面必须给一个新的小情境，不得照抄原文例题。
5. 每张卡写 source：它依据源材料的哪一段/哪条规则（一句话）。
6. 数量 8-14 张：概念不超过一半；至少 2 张 application；材料覆盖不到的深度不出卡。
7. 严禁互相提示的卡片（一张卡的背面出现另一张卡的答案线索）。

## 调度契约

复习调度使用 FSRS（open-spaced-repetition/py-fsrs，MIT）。卡片以 Card.to_dict() 形式
持久化调度状态；评分为 1=Again 2=Hard 3=Good 4=Easy。首次到期时间由调度器给出，
本技能只负责卡片内容质量。

## 输出格式

只输出 JSON：{"cards": [{"front": "...", "back": "...", "kind": "concept|fact|application", "source": "..."}]}。
不要输出 JSON 之外的任何文字。
