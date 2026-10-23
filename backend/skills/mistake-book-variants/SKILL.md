---
name: mistake-book-variants
description: 把判分后的错题加工成错题本——错误诊断（错误类型/误区/补救）加同技能变式题，变式必须换表面特征但保持同一技能目标。
---

# 错题本与变式题设计法

## 错误诊断规则

1. 每道错题给三层诊断：error_type（概念误用 / 程序性错误 / 审题偏差 / 计算疏漏，四选一）、
   misconception（一句话说出学习者很可能真实相信的错误信念）、remediation（一条可执行的补救动作，
   指向源材料的具体规则，不重复题面）。
2. diagnosis 只依据：题面、学习者作答、正确答案、评分反馈。不得臆测学习者的其他历史。
3. 学习者未作答的题诊断写「未作答」，remediation 指向该技能的入门入口而不是难题技巧。

## 变式题规则

1. 每道错题 2 道变式：一道近迁移（换数值/换措辞，同一技能路径），一道远迁移（换情境载体，
   技能目标不变，难度不高于原题太多）。
2. 变式必须改变原题的表面特征（数字、情境、问法顺序），但解题所需的技能必须与错题相同。
3. 变式题给 answer 与一句话 explanation；answer 必须由题面可直接推出，出题后自行验算一遍。
4. 客观题（选择/判断/填空）变式保持同题型；主观题变式给要点式 answer。

## 输出格式

只输出 JSON：{"mistakes": [{"index": 0, "error_type": "...", "misconception": "...",
"remediation": "...", "variants": [{"type": "choice|short_answer", "question": "...",
"options": ["..."](仅choice), "answer": "...", "explanation": "..."}]}]}。
index 对应输入错题在 mistakes 输入数组中的下标。不要输出 JSON 之外的任何文字。
