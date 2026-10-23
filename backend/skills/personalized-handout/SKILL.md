---
name: personalized-handout
description: 为具体学习者生成个性化讲义或实操指南的完整方法论——基于可观察的支撑水平决定支架密度，基于确认的表达偏好决定解释顺序，内容必须只使用给定知识节点的声明事实。
---

# 个性化讲义 / 实操指南生成法

## 输入契约

你会收到：知识节点事实（title、description、learning_goal、常见误解、证据要求、领域种子事实）、
学习者投影（支撑水平 L1-L5、最近证据、确认的表达偏好）。**只允许使用这些事实组织内容**；
节点事实没有覆盖的概念一律不写，来源行原样保留调用方提供的 source_refs。

## 内容规则

1. 每个讲义 4-6 个内容块，块类型只能是 HEADING / CONCEPT / EXAMPLE / CALLOUT / CHECKPOINT / TASK / SOURCE。
2. EXAMPLE 必须是可运行的最小代码或具体数值演算，不得出现节点事实之外的 API 或概念。
3. 每个解释句先给结论再给依据；禁止「简单来说」「值得注意的是」等填充语。
4. CHECKPOINT 只考察本讲义刚建立的一个判断，要求学习者写出理由而不是选择题。
5. 高支架（L1-L2 或最近一次证据为 incorrect）：先读后判，例子最小化，CHECKPOINT 放在概念块之后。
6. 低支架（L3+ 且证据正确）：先给一个变形/边界例子再收回规则，CHECKPOINT 考迁移而非复述。

## 偏好排序

- example_first：EXAMPLE 在 CONCEPT 之前，规则从例子中收回。
- rule_then_practice：CONCEPT 在前，EXAMPLE 紧跟其后，最后必须有一个迁移 TASK。

## 误区处理

common_misconceptions 中与当前节点相关的条目必须显式出现在一个 CALLOUT 里，
写法是「可见的现象 → 为什么它会误导 → 正确的区分标准」，不得只写「这是错的」。

## 输出格式

只输出 JSON：{"title": "...", "blocks": [{"type": "...", "text": "...", "level": 1(仅HEADING)}]}。
text 中代码用 ```python 围栏；不要输出 JSON 之外的任何文字。
