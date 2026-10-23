from __future__ import annotations

from .enums import InteractionKind, NarrativeGrammar
from .models import (
    NarrativeBeats,
    NarrativeBlueprint,
    NarrativeRequest,
    OppositionEmbodiment,
    RouteFrame,
    RouteFrames,
    SupplyBeats,
    WorldAnomaly,
    WorldMetaphor,
)


_ACTIONS: dict[InteractionKind, str] = {
    InteractionKind.EXPLAIN: "对照卷轴线索，用自己的话解释规则与现象之间的关系",
    InteractionKind.ORDERING: "重新排列被打乱的碎片，并说明相邻步骤为什么必须这样连接",
    InteractionKind.STEP_COMPLETION: "补齐缺失步骤，再检查完整过程能否成立",
    InteractionKind.COMPARE: "并排比较两个线索，指出真正改变结论的条件",
    InteractionKind.FIND_ERROR: "沿异常痕迹定位错误，并说明它为什么会影响结果",
    InteractionKind.COUNTEREXAMPLE: "找出能推翻错误说法的反例，并解释冲突所在",
    InteractionKind.APPLICATION: "把已经确认的规则用于新的情境，并说明适用条件",
    InteractionKind.CODE_TASK: "检查控制逻辑与边界条件，修复后验证运行结果",
    InteractionKind.REWRITE: "重新组织表达，使主旨、依据与结论重新对应",
    InteractionKind.OPEN_RESPONSE: "完整说明判断依据，让场景根据解释给出反馈",
    InteractionKind.MULTIPLE_CHOICE: "比较候选方案的条件与后果，再作出选择",
}


_ANOMALIES: dict[NarrativeGrammar, tuple[str, str]] = {
    NarrativeGrammar.DISCOVER_RULE: ("规律留下的光纹彼此不同，却没有显出共同的变化线索", "UNKNOWN_PATTERN"),
    NarrativeGrammar.REPAIR_MECHANISM: ("运行节奏不断错位，关键部件无法按正确关系协同", "MALFUNCTION"),
    NarrativeGrammar.EXPOSE_ILLUSION: ("两组相似线索交换标签，让错误说法看起来像正确规律", "MISLEADING_CLAIM"),
    NarrativeGrammar.COMPLETE_MISSING_LINK: ("关键条件从线索中缺失，完整路径因此无法显现", "MISSING_CONDITION"),
    NarrativeGrammar.RECONSTRUCT_CHAIN: ("步骤和因果顺序被打乱，前后结果无法重新接合", "BROKEN_CHAIN"),
    NarrativeGrammar.TRANSFER_ACROSS_BORDER: ("旧规则来到新情境后失去表面提示，需要重新确认适用方式", "UNFAMILIAR_CONTEXT"),
}


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_fallback_blueprint(request: NarrativeRequest) -> NarrativeBlueprint:
    frame = request.frame
    affected = frame.allowed_objects[0]
    second = frame.allowed_objects[1]
    anomaly, opposition_form = _ANOMALIES[frame.grammar]
    understanding_action = _ACTIONS[request.route_tasks.understanding_trial.interaction_kind]
    practice_action = _ACTIONS[request.route_tasks.practice_trial.interaction_kind]

    world_rule = _clip(
        f"在{frame.primary_scene_name}中，{request.core_concept.name}决定线索如何变化："
        f"{request.core_concept.explanation}",
        140,
    )
    alignment = _clip(
        f"世界规律直接对应“{request.core_concept.name}”；叙事只改变呈现方式，不改变其适用范围："
        f"{request.core_concept.applicability}",
        200,
    )

    return NarrativeBlueprint(
        world_metaphor=WorldMetaphor(world_rule=world_rule, alignment_note=alignment),
        world_anomaly=WorldAnomaly(
            affected_object=affected,
            visible_change=f"{frame.primary_scene_name}里的{affected}{anomaly}，{second}也随之失去稳定回应。",
            local_stakes=f"如果{affected}继续异常，当前地点通往下一处的路线就不会显现。",
        ),
        route_frames=RouteFrames(
            understanding=RouteFrame(
                action_frame=f"围绕{affected}，{understanding_action}。",
                success_response=f"解释成立后，{affected}显出稳定的微光，相关线索重新对齐。",
                failure_signal=f"证据仍不完整时，{affected}只显出一部分变化，等待重新观察。",
            ),
            practice=RouteFrame(
                action_frame=f"借助{second}，{practice_action}。",
                success_response=f"操作或判断通过验证后，{second}恢复正常节奏并点亮前路。",
                failure_signal=f"结果与规则不一致时，{second}保留错位痕迹，提示需要检查条件。",
            ),
        ),
        opposition_embodiment=OppositionEmbodiment(
            form=opposition_form,
            appearance=f"一团依附在{affected}附近的墨色扰动",
            behaviour="它不断遮住关键条件、交换相似线索或打乱正确顺序，让错误判断更难被发现。",
            transformation=f"知识行动完成后，墨色扰动从{affected}上脱落，转化成可收藏的旅程印记。",
        ),
        beats=NarrativeBeats(
            opening=f"{frame.primary_scene_name}里，{affected}突然失去稳定回应，周围线索也开始错位。",
            rule_reveal=f"{second}显出一段与“{request.core_concept.name}”有关的旧记录，说明异常必须通过知识行动解决。",
            first_trial=f"玩家完成本路线的第一次知识行动后，{affected}立即根据证据强弱产生可见变化。",
            supply=SupplyBeats(
                success_respite=f"{affected}暂时稳定，柿小吉陪玩家整理刚刚确认的方法，再准备进入交错挑战。",
                remediation_reframe=f"{affected}只保留最关键的异常痕迹，让补救任务从更小的证据缺口重新开始。",
            ),
            interleave_turn=f"同一规律换成新的表现形式，玩家必须离开原来的表面线索，再次使用已经确认的方法。",
            boss_reveal="普通阻力退入核心封印场，错误逻辑被分成解释、辨析与迁移三层封印。",
            resolution=f"三层验证完成后，{affected}与{second}恢复稳定，旅程成果被送往丰穗宝库与烟火高台。",
        ),
        continuity_keys=[affected, second],
    )
