from __future__ import annotations

from .enums import NarrativeGrammar, OppositionForm
from .models import SceneDefinition


SCENE_CATALOG_VERSION = "kaki-scene-registry-20260801"


SCENES: dict[str, SceneDefinition] = {
    "camp": SceneDefinition(
        scene_id="camp",
        display_name="微光营地",
        learning_affordances=["确定目标", "观察旅途线索", "重新选择目的地"],
        interactive_objects=["营灯", "卷轴桌", "路线卷轴", "柿叶"],
    ),
    "echo_archive": SceneDefinition(
        scene_id="echo_archive",
        display_name="回声书阁",
        learning_affordances=["规则提取", "复述解释", "闭卷回忆"],
        interactive_objects=["悬浮书页", "回声铃", "卷轴台", "旧墨文字"],
    ),
    "mirror_lake": SceneDefinition(
        scene_id="mirror_lake",
        display_name="镜湖",
        learning_affordances=["例子对照", "错误校准", "迁移判断"],
        interactive_objects=["水面倒影", "水纹", "轨迹", "月桂标记", "微光裂纹"],
    ),
    "mechanism_workshop": SceneDefinition(
        scene_id="mechanism_workshop",
        display_name="机关工坊",
        learning_affordances=["方法选择", "动手练习", "因果拆解"],
        interactive_objects=["木轮机关", "刻度", "控制台", "齿轮", "工具台"],
    ),
    "mist_valley": SceneDefinition(
        scene_id="mist_valley",
        display_name="雾谷",
        learning_affordances=["证据补全", "针对性补救", "挑战作答"],
        interactive_objects=["雾中石碑", "墨迹", "碎光路标", "证据缺口", "道路"],
    ),
    "transfer_bridge": SceneDefinition(
        scene_id="transfer_bridge",
        display_name="迁移之桥",
        learning_affordances=["交错练习", "跨情境迁移", "综合回忆"],
        interactive_objects=["桥面符纹", "远端装置", "迁移路牌", "云海", "灯火"],
    ),
    "boss_core": SceneDefinition(
        scene_id="boss_core",
        display_name="核心封印场",
        learning_affordances=["综合解释", "关键辨析", "最终迁移"],
        interactive_objects=["封印残环", "暮色灯台", "知识核心", "核心石座"],
    ),
    "harvest_treasury": SceneDefinition(
        scene_id="harvest_treasury",
        display_name="丰穗宝库与烟火高台",
        learning_affordances=["成果回望", "奖励选择", "旅程收束"],
        interactive_objects=["展示台", "旅程印记", "丰穗灯", "烟火高台"],
    ),
}


GRAMMAR_SCENES: dict[NarrativeGrammar, tuple[str, str]] = {
    NarrativeGrammar.DISCOVER_RULE: ("echo_archive", "mirror_lake"),
    NarrativeGrammar.REPAIR_MECHANISM: ("mechanism_workshop", "mist_valley"),
    NarrativeGrammar.EXPOSE_ILLUSION: ("mirror_lake", "mist_valley"),
    NarrativeGrammar.COMPLETE_MISSING_LINK: ("mist_valley", "echo_archive"),
    NarrativeGrammar.RECONSTRUCT_CHAIN: ("mechanism_workshop", "echo_archive"),
    NarrativeGrammar.TRANSFER_ACROSS_BORDER: ("transfer_bridge", "mirror_lake"),
}


GRAMMAR_OPPOSITION_FORMS: dict[NarrativeGrammar, list[OppositionForm]] = {
    NarrativeGrammar.DISCOVER_RULE: [OppositionForm.UNKNOWN_PATTERN, OppositionForm.MISSING_CONDITION],
    NarrativeGrammar.REPAIR_MECHANISM: [OppositionForm.MALFUNCTION, OppositionForm.BROKEN_CHAIN],
    NarrativeGrammar.EXPOSE_ILLUSION: [OppositionForm.MISLEADING_CLAIM, OppositionForm.UNKNOWN_PATTERN],
    NarrativeGrammar.COMPLETE_MISSING_LINK: [OppositionForm.MISSING_CONDITION, OppositionForm.MISLEADING_CLAIM],
    NarrativeGrammar.RECONSTRUCT_CHAIN: [OppositionForm.BROKEN_CHAIN, OppositionForm.MALFUNCTION],
    NarrativeGrammar.TRANSFER_ACROSS_BORDER: [OppositionForm.UNFAMILIAR_CONTEXT, OppositionForm.UNKNOWN_PATTERN],
}


def scene(scene_id: str) -> SceneDefinition:
    try:
        return SCENES[scene_id]
    except KeyError as error:
        raise ValueError(f"unknown scene id: {scene_id}") from error


def public_scene_ids() -> tuple[str, ...]:
    return tuple(key for key in SCENES if key not in {"boss_core", "harvest_treasury"})
