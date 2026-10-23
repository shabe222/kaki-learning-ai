"""Bounded basic-physics Domain Knowledge Pack: force, motion, and momentum."""
from __future__ import annotations

from typing import Any, Mapping

from services.darya_domain_knowledge import (
    CandidateTemplate,
    DomainKnowledgePack,
    EvidenceRule,
    GoalResolutionRule,
    KnowledgeNode,
    ResourceSeed,
    ReviewerPolicy,
)


DOMAIN_ID = "basic_physics_force_motion_momentum_v1"
DOMAIN_TITLE = "基础物理：力、运动与动量"
DOMAIN_SCOPE = "经典力学入门中的相互作用、合力与加速度、牛顿定律、受力分析、作用反作用、冲量动量与动量守恒。"
DOMAIN_PROVENANCE = {
    "kind": "stable_introductory_physics",
    "source": "bounded editorial contract aligned with standard introductory classical mechanics",
    "external_search_required": False,
}
SOURCE_CATALOG = (
    {
        "source_id": "openstax-university-physics-v1-5.3",
        "title": "University Physics Volume 1: Newton's Second Law",
        "publisher": "OpenStax, Rice University",
        "version": "2016 web edition",
        "locator": "Section 5.3",
        "claim_scope": "systems, external forces, net force, acceleration, mass, momentum form of Newton's second law",
        "url": "https://openstax.org/books/university-physics-volume-1/pages/5-3-newtons-second-law",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "publisher textbook page review"},
    },
    {
        "source_id": "openstax-university-physics-v1-5.5",
        "title": "University Physics Volume 1: Newton's Third Law",
        "publisher": "OpenStax, Rice University",
        "version": "2016 web edition",
        "locator": "Section 5.5",
        "claim_scope": "interaction pairs, equal and opposite forces on different bodies",
        "url": "https://openstax.org/books/university-physics-volume-1/pages/5-5-newtons-third-law",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "publisher textbook page review"},
    },
    {
        "source_id": "openstax-university-physics-v1-9.2",
        "title": "University Physics Volume 1: Impulse and Collisions",
        "publisher": "OpenStax, Rice University",
        "version": "2016 web edition",
        "locator": "Section 9.2, Impulse-Momentum Theorem",
        "claim_scope": "impulse, force-time effect, change in momentum, mass and velocity implications",
        "url": "https://openstax.org/books/university-physics-volume-1/pages/9-2-impulse-and-collisions",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "publisher textbook page review"},
    },
    {
        "source_id": "openstax-university-physics-v1-9.3",
        "title": "University Physics Volume 1: Conservation of Linear Momentum",
        "publisher": "OpenStax, Rice University",
        "version": "2016 web edition",
        "locator": "Section 9.3, Requirements for Momentum Conservation",
        "claim_scope": "system boundary, net external force/impulse, conservation of total momentum",
        "url": "https://openstax.org/books/university-physics-volume-1/pages/9-3-conservation-of-linear-momentum",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "publisher textbook page review"},
    },
)
CLAIM_SOURCE_MAP = {
    "force_as_interaction": ("openstax-university-physics-v1-5.3",),
    "balanced_forces": ("openstax-university-physics-v1-5.3",),
    "net_force_acceleration": ("openstax-university-physics-v1-5.3",),
    "mass_response": ("openstax-university-physics-v1-5.3",),
    "inertia_zero_net_force": ("openstax-university-physics-v1-5.3",),
    "acceleration_changes_velocity": ("openstax-university-physics-v1-5.3",),
    "single_body_boundary": ("openstax-university-physics-v1-5.3",),
    "external_forces": ("openstax-university-physics-v1-5.3",),
    "third_law_pair": ("openstax-university-physics-v1-5.5",),
    "different_bodies": ("openstax-university-physics-v1-5.5",),
    "impulse_momentum_change": ("openstax-university-physics-v1-9.2",),
    "mass_velocity_change": ("openstax-university-physics-v1-9.2",),
    "system_external_impulse": ("openstax-university-physics-v1-9.3",),
    "system_boundary": ("openstax-university-physics-v1-9.3",),
}

P1 = "physics:interaction_force"
P2 = "physics:net_force_acceleration"
P3 = "physics:newton_laws"
P4 = "physics:free_body_reasoning"
P5 = "physics:action_reaction"
P6 = "physics:momentum_impulse"
P7 = "physics:momentum_conservation"


def _templates(
    *, low: CandidateTemplate, misconception: CandidateTemplate,
    correct: CandidateTemplate, revised: CandidateTemplate,
) -> tuple[Mapping[str, CandidateTemplate], CandidateTemplate]:
    return {
        "uncertain": low,
        "incorrect": misconception,
        "correct": correct,
        "partial": correct,
    }, revised


_p1_templates, _p1_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先把力看成相互作用。", "一本书在桌面上静止时，书受到了哪些物体的作用？", "逐个指出施力物体。", claim_keys=("force_as_interaction",)),
    misconception=CandidateTemplate("targeted_question", "这里把力当成了运动物体自带的东西。", "一辆车匀速直线行驶时，是否一定有一个向前的合力维持速度？", "先区分速度和速度变化。", claim_keys=("force_as_interaction",)),
    correct=CandidateTemplate("transfer_question", "相互作用的来源已经能找到了。", "物体匀速运动时仍可能同时受多个力吗？合力应满足什么条件？", claim_keys=("force_as_interaction", "balanced_forces")),
    revised=CandidateTemplate("targeted_question", "只追踪谁对谁施力。", "书静止在桌面上时，重力和支持力分别由谁施加在谁身上？", "先写施力物体，再写受力物体。", claim_keys=("force_as_interaction",)),
)

_p2_templates, _p2_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先只看合力与速度变化。", "同一物体受到恒定合力时，直接被决定的是速度还是加速度？", "想一想哪一个量描述速度的变化。", claim_keys=("net_force_acceleration",)),
    misconception=CandidateTemplate("targeted_question", "这里把速度大小和加速度混在了一起。", "同样大小的合力分别作用在轻车和重车上，哪一辆加速度更大？", "用合力、质量和加速度的关系判断。", claim_keys=("net_force_acceleration", "mass_response")),
    correct=CandidateTemplate("transfer_question", "合力与加速度的关系已经说清了。", "若合力不变而质量变为两倍，加速度怎样变化？为什么？", claim_keys=("net_force_acceleration", "mass_response")),
    revised=CandidateTemplate("targeted_question", "只比较合力、质量和加速度。", "同样的合力作用下，质量越大的物体加速度更大还是更小？", "不要用速度大小替代加速度。", claim_keys=("net_force_acceleration",)),
)

_p3_templates, _p3_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先用第一定律检查合力。", "物体做匀速直线运动时，合力一定沿运动方向吗？", "判断速度是否在改变。", claim_keys=("inertia_zero_net_force",)),
    misconception=CandidateTemplate("targeted_question", "这里把“运动”误当成必须持续受力。", "若冰面上的滑块近似不受合力，它会立刻停下还是保持匀速？", "先忽略摩擦，再用惯性判断。", claim_keys=("inertia_zero_net_force",)),
    correct=CandidateTemplate("transfer_question", "第一、第二定律的分工已经能用了。", "一辆车转弯但速率不变时，加速度和合力是否为零？为什么？", claim_keys=("inertia_zero_net_force", "acceleration_changes_velocity")),
    revised=CandidateTemplate("targeted_question", "只看速度矢量有没有变化。", "物体若方向或速率都不变，合力应为零还是非零？", "运动本身不要求合力，速度变化才要求。", claim_keys=("inertia_zero_net_force",)),
)

_p4_templates, _p4_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先选定一个受力对象。", "分析斜面上的木块时，受力图里只画作用在木块上的力，还是也画木块对斜面的力？", "每一支箭头都要落在同一个受力对象上。", claim_keys=("single_body_boundary",)),
    misconception=CandidateTemplate("targeted_question", "这里把不同物体上的力画进了同一张图。", "木块受斜面支持力；它的反作用力也画在木块的受力图上吗？", "先问这个力究竟作用在哪个物体上。", claim_keys=("single_body_boundary",), assumed_prerequisites=(P1,)),
    correct=CandidateTemplate("transfer_question", "受力对象的边界已经能守住了。", "换成绳子拉箱子：箱子的受力图里应保留哪些外力，哪些力属于别的物体？", claim_keys=("single_body_boundary", "external_forces")),
    revised=CandidateTemplate("targeted_question", "只画作用在目标物体上的力。", "以木块为对象，重力、支持力和木块对斜面的力中，哪几个应画入木块受力图？", "逐个检查受力物体是不是木块。", claim_keys=("single_body_boundary",)),
)

_p5_templates, _p5_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先找同一次相互作用中的两个物体。", "人推墙时，墙对人的力与人对墙的力分别作用在哪个物体上？", "不要把它们画到同一个物体上抵消。", claim_keys=("third_law_pair",)),
    misconception=CandidateTemplate("targeted_question", "这里把质量大小当成了相互作用力大小。", "卡车与小车碰撞时，卡车对小车的力是否比小车对卡车的力更大？", "只比较同一相互作用中的一对力。", claim_keys=("third_law_pair",), assumed_prerequisites=(P1,)),
    correct=CandidateTemplate("transfer_question", "作用反作用的对象边界已经清楚了。", "人与地面相互作用时，这一对力为什么不会在人的受力图里互相抵消？", claim_keys=("third_law_pair", "different_bodies")),
    revised=CandidateTemplate("targeted_question", "只比较同一对相互作用力。", "碰撞时两车互相施加的力，大小、方向和受力物体分别怎样？", "先写两个不同的受力物体。", claim_keys=("third_law_pair", "different_bodies")),
)

_p6_templates, _p6_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先把冲量看成一段时间内的力效果。", "相同平均力作用 1 秒和 3 秒，哪种情况带来的动量变化更大？", "同时看力和作用时间。", claim_keys=("impulse_momentum_change",)),
    misconception=CandidateTemplate("targeted_question", "这里漏掉了力的作用时间。", "一个较小的力作用更长时间，能否产生与较大力短时作用相同的冲量？", "比较力与时间的乘积。", claim_keys=("impulse_momentum_change",)),
    correct=CandidateTemplate("transfer_question", "冲量与动量变化已经连起来了。", "两个质量不同的物体受到相同冲量时，动量变化是否相同？速度变化呢？", claim_keys=("impulse_momentum_change", "mass_velocity_change")),
    revised=CandidateTemplate("targeted_question", "只比较力、时间和动量变化。", "平均力相同但作用时间加倍时，冲量和动量变化怎样改变？", "先不要跳到速度变化。", claim_keys=("impulse_momentum_change",)),
)

_p7_templates, _p7_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先选系统，再检查外界冲量。", "判断两车碰撞系统动量是否守恒前，最先要检查系统边界还是碰撞是否弹性？", "动量守恒先看合外力冲量。", claim_keys=("system_external_impulse",)),
    misconception=CandidateTemplate("targeted_question", "这里把动量守恒和能量守恒混在了一起。", "只要发生碰撞，总动量和机械能就都会守恒吗？", "先检查系统的合外力冲量。", secondary_concepts=("energy_conservation",), claim_keys=("system_external_impulse",)),
    correct=CandidateTemplate("transfer_question", "系统边界与外界冲量已经能检查了。", "若把其中一辆车单独作为系统，它的动量在碰撞中还守恒吗？为什么？", claim_keys=("system_external_impulse", "system_boundary")),
    revised=CandidateTemplate("targeted_question", "只判断总动量，不混入机械能。", "以两车整体为系统，碰撞时间内合外力冲量近似为零时，碰撞前后哪个总量保持不变？", "先写系统边界和外界冲量。", claim_keys=("system_external_impulse",)),
)


NODES: dict[str, KnowledgeNode] = {
    P1: KnowledgeNode(
        P1, "相互作用与力", "把力识别为物体间相互作用，并区分受力物体与施力物体。", (),
        "能指出力的施力物体和受力物体，并用合力描述运动状态是否改变。",
        ("识别施力与受力物体", "在匀速情形中区分多个力与合力"),
        ("运动物体必然有沿运动方向的力", "力是物体自带的属性"),
        "introductory", ("相互作用", "力的概念", "施力", "受力", "interaction", "force concept"),
        (
            EvidenceRule(r"(?:有速度|在运动).{0,30}(?:就|所以|一定).{0,25}(?:向前|运动方向).{0,10}(?:有力|受力)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把力当成维持运动的物体属性。", "尚未把力理解为相互作用，也未区分速度与速度变化", .9, "学习者明确说运动本身要求沿运动方向受力。"),
            EvidenceRule(r"(?:力|受力).{0,35}(?:物体间|相互作用|谁对谁)|(?:施力物体|受力物体).{0,45}(?:不同|指出)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者把力描述为物体间相互作用，并能区分施力与受力对象。", "规则已能解释，但仍需迁移到合力情形", .87, "回答明确了相互作用双方。", ("力来自物体间相互作用",)),
            EvidenceRule(r"(?:匀速|速度不变).{0,55}(?:多个力|同时受力).{0,45}(?:合力为零|平衡)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能把力的相互作用观点迁移到受多个平衡力的情形。", "相互作用与合力概念已达到可继续使用的证据水平", .92, "回答区分了单个力与合力。", ("匀速不代表不受任何力",)),
        ), _p1_templates, _p1_revised, ReviewerPolicy(("force_as_interaction", "balanced_forces"), required_prompt_terms=("力", "物体")),
        "把它想成接口调用：只有明确谁向谁发出作用，才有一条可追踪的关系。", DOMAIN_PROVENANCE,
    ),
    P2: KnowledgeNode(
        P2, "合力与加速度", "用合力和质量判断加速度，而不是用速度大小替代加速度。", (P1,),
        "能用合力、质量和加速度的关系比较运动变化。",
        ("说明合力决定加速度", "迁移到质量变化的比较"),
        ("没有力物体就会停下", "力越大速度一定越大"),
        "basic", ("合力", "加速度", "F=ma", "质量", "net force", "acceleration"),
        (
            EvidenceRule(r"(?:没有力|合力为零).{0,30}(?:就会|一定).{0,20}(?:停下|静止)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者认为零合力必然让物体停止。", "尚未区分速度与加速度", .9, "回答把零加速度误作零速度。", blocking_prerequisite=P1),
            EvidenceRule(r"(?:合力|净力).{0,35}(?:决定|产生|等于).{0,30}加速度|F\s*=\s*ma", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者把合力与加速度联系起来。", "关系已能解释，但质量变体尚未观察", .87, "回答使用了合力而非单个力。", ("合力决定速度如何变化",)),
            EvidenceRule(r"(?:同样|相同).{0,18}(?:合力|力).{0,45}(?:质量越大|重车).{0,35}(?:加速度越小|变化更慢)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能迁移到相同合力、不同质量的比较。", "合力与加速度已达到可继续使用的证据水平", .92, "回答正确比较了质量对加速度的影响。", ("相同合力下质量越大加速度越小",)),
        ), _p2_templates, _p2_revised, ReviewerPolicy(("net_force_acceleration", "mass_response"), required_prompt_terms=("合力", "加速度", "质量")),
        "像同一批计算资源处理不同负载：输入相同，负载越大，状态改变得越慢。", DOMAIN_PROVENANCE,
    ),
    P3: KnowledgeNode(
        P3, "牛顿定律的运动判断", "用惯性、合力与加速度判断速度矢量是否改变。", (P2,),
        "能区分保持运动状态与改变运动状态所需的条件。",
        ("用零合力解释匀速直线运动", "迁移到方向改变的运动"),
        ("维持运动必须持续施力",),
        "basic", ("牛顿定律", "惯性", "第一定律", "第二定律", "转弯", "Newton"),
        (
            EvidenceRule(r"(?:维持|保持).{0,20}(?:运动|匀速).{0,25}(?:必须|需要).{0,18}(?:力|推力)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者认为维持匀速必须有沿运动方向的合力。", "惯性与零合力条件尚未稳定", .91, "学习者明确把力当成维持速度的原因。", blocking_prerequisite=P2),
            EvidenceRule(r"(?:合力为零|零合力).{0,45}(?:静止|匀速直线|速度不变)|(?:惯性).{0,45}(?:保持|运动状态)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者能用零合力和惯性解释运动状态保持。", "定律已能解释，但方向变化尚未观察", .88, "回答正确区分零合力与零速度。", ("零合力意味着加速度为零",)),
            EvidenceRule(r"(?:转弯|方向改变).{0,45}(?:加速度|合力).{0,25}(?:不为零|存在)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能把速度矢量变化迁移到等速转弯。", "牛顿定律判断已达到可继续使用的证据水平", .92, "回答认识到方向改变也是加速度。", ("速率不变也可能有加速度",)),
        ), _p3_templates, _p3_revised, ReviewerPolicy(("inertia_zero_net_force", "acceleration_changes_velocity"), required_prompt_terms=("合力", "速度", "加速度")),
        "像系统没有新输入时保持原状态；改变方向或速率才说明存在净输入。", DOMAIN_PROVENANCE,
    ),
    P4: KnowledgeNode(
        P4, "受力分析", "固定一个受力对象，只列出外界实际作用在它上面的力。", (P3,),
        "能用文本列出目标物体的受力，并排除作用在别的物体上的力。",
        ("明确受力对象", "列出力的来源与方向", "排除不作用于目标物体的力"),
        ("把作用反作用力画在同一物体上", "把运动方向当成一个力"),
        "intermediate", ("受力分析", "受力图", "自由体", "斜面", "free body", "FBD"),
        (
            EvidenceRule(r"(?:作用反作用|反作用力).{0,40}(?:同一|一个).{0,20}(?:物体|受力图).{0,20}(?:抵消|画)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把作用在不同物体上的一对力放进同一受力对象。", "受力对象边界尚未稳定", .91, "回答明确混合了不同受力物体。", blocking_prerequisite=P1),
            EvidenceRule(r"(?:只画|只列).{0,35}(?:作用在|施加在).{0,25}(?:目标|这个|木块|物体).{0,20}(?:上|的力)|(?:受力对象).{0,40}(?:固定|同一个)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者能固定单一受力对象。", "受力边界已能解释，但新情境尚未观察", .88, "回答明确只保留作用在目标物体上的力。", ("受力图只包含作用于目标物体的力",)),
            EvidenceRule(r"(?:绳|斜面|箱子|木块).{0,80}(?:重力|支持力|拉力).{0,60}(?:不画|排除|别的物体)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能在新情境中列出并筛除外力。", "受力分析已达到可继续使用的证据水平", .91, "回答同时给出真实外力和对象边界。", ("每个力都有可指出的施力物体",)),
        ), _p4_templates, _p4_revised, ReviewerPolicy(("single_body_boundary", "external_forces"), required_prompt_terms=("物体", "力", "作用")),
        "像排查一个服务：先固定当前服务边界，只列真正进入这个服务的输入。", DOMAIN_PROVENANCE,
    ),
    P5: KnowledgeNode(
        P5, "作用与反作用", "识别大小相等、方向相反且作用在不同物体上的一对相互作用力。", (P1,),
        "能正确配对第三定律中的两个力，并说明它们为何不在单个物体上抵消。",
        ("指出两个不同受力物体", "比较同一相互作用力的大小与方向"),
        ("质量大的物体施力更大", "作用反作用在同一物体上抵消"),
        "basic", ("作用反作用", "第三定律", "碰撞力", "互相施力", "action reaction", "third law"),
        (
            EvidenceRule(r"(?:卡车|质量大|大物体).{0,35}(?:力更大|作用力大)|(?:小车|质量小).{0,35}(?:力更小|受力小)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者认为质量大小决定第三定律力对的大小不相等。", "尚未识别同一相互作用中的力对", .92, "回答明确说大物体施加更大的碰撞力。", blocking_prerequisite=P1),
            EvidenceRule(r"(?:大小相等|一样大).{0,30}(?:方向相反).{0,40}(?:不同物体|分别作用)|(?:不同物体).{0,45}(?:大小相等|方向相反)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者正确说明第三定律力对。", "力对规则已能解释，但迁移尚未观察", .89, "回答覆盖大小、方向和不同受力物体。", ("作用反作用力作用在不同物体上",)),
            EvidenceRule(r"(?:人和地面|两车|碰撞).{0,65}(?:不同物体|不在同一受力图|不会抵消).{0,45}(?:相等|相反|力)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能把第三定律迁移到另一相互作用。", "作用反作用已达到可继续使用的证据水平", .92, "回答解释了为何不在单个物体上抵消。", ("力对不在单一物体上相加",)),
        ), _p5_templates, _p5_revised, ReviewerPolicy(("third_law_pair", "different_bodies"), required_prompt_terms=("力", "物体", "大小")),
        "像一次请求与响应分别落在两个端点：相关但不在同一个对象内部互相取消。", DOMAIN_PROVENANCE,
    ),
    P6: KnowledgeNode(
        P6, "冲量与动量变化", "把一段时间内的合力作用与系统动量变化联系起来。", (P2,),
        "能用冲量解释动量变化，并区分动量变化与速度变化。",
        ("同时考虑力和作用时间", "迁移到不同质量物体"),
        ("冲量只由力大小决定", "相同冲量带来相同速度变化"),
        "intermediate", ("动量", "冲量", "力乘时间", "碰撞时间", "momentum", "impulse"),
        (
            EvidenceRule(r"(?:冲量|动量变化).{0,35}(?:只看|只由).{0,20}(?:力|力大小)|(?:作用时间).{0,25}(?:无关|不重要)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者忽略了力的作用时间。", "尚未把冲量与动量变化联系完整", .91, "回答明确说冲量只由力大小决定。", blocking_prerequisite=P2),
            EvidenceRule(r"(?:冲量|力乘时间|F.{0,5}t).{0,45}(?:动量变化|Δp)|(?:动量变化).{0,45}(?:力|作用时间|冲量)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者把冲量与动量变化联系起来。", "冲量规则已能解释，但质量变体尚未观察", .88, "回答同时提到力、时间和动量变化。", ("冲量等于动量变化",)),
            EvidenceRule(r"(?:相同冲量|同样冲量|冲量相同|冲量一样).{0,45}(?:动量变化相同|动量变化一样|Δp相同).{0,55}(?:质量|速度变化).{0,30}(?:不同|不一样|越大越小)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能区分相同动量变化与不同速度变化。", "冲量与动量已达到可继续使用的证据水平", .93, "回答把规则迁移到不同质量物体。", ("相同冲量产生相同动量变化",)),
        ), _p6_templates, _p6_revised, ReviewerPolicy(("impulse_momentum_change", "mass_velocity_change"), required_prompt_terms=("冲量", "动量", "时间")),
        "像累计处理量：一次输入的强度与持续时间共同决定状态总共改变多少。", DOMAIN_PROVENANCE,
    ),
    P7: KnowledgeNode(
        P7, "动量守恒", "先选系统，再用合外力冲量判断系统总动量是否守恒。", (P6,),
        "能在碰撞或分离情境中选择系统边界并判断总动量守恒条件。",
        ("明确系统边界", "检查合外力冲量", "比较系统总动量而非单个物体"),
        ("任何碰撞机械能和动量都一起守恒", "单个物体在碰撞中也动量守恒"),
        "intermediate", ("动量守恒", "系统", "外力冲量", "碰撞", "conservation of momentum"),
        (
            EvidenceRule(r"(?:任何|只要|所有).{0,15}(?:碰撞).{0,35}(?:动量|机械能).{0,25}(?:都|一定).{0,12}守恒|(?:单个物体).{0,30}(?:碰撞).{0,20}动量守恒", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者没有先选系统和检查合外力冲量。", "尚未用系统边界和外界冲量判断动量守恒", .92, "回答把碰撞本身当成无条件守恒保证。", blocking_prerequisite=P6),
            EvidenceRule(r"(?:系统|整体).{0,45}(?:合外力冲量|外界冲量).{0,30}(?:为零|近似为零).{0,35}(?:总动量|动量).{0,20}守恒|(?:总动量).{0,40}(?:系统边界|外力冲量)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者用系统边界和外界冲量说明动量守恒条件。", "守恒条件已能解释，但边界变体尚未观察", .9, "回答包含系统、合外力冲量和总动量。", ("系统合外力冲量近似为零时总动量守恒",)),
            EvidenceRule(r"(?:单独|只把).{0,35}(?:一辆车|一个物体).{0,35}(?:不守恒|外力冲量不为零).{0,55}(?:两车|整体|系统).{0,20}(?:守恒|外冲量)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能通过改变系统边界重新判断动量守恒。", "动量守恒已达到可继续使用的证据水平", .94, "回答对比了单个物体与整体系统。", ("守恒判断依赖系统边界",)),
        ), _p7_templates, _p7_revised, ReviewerPolicy(("system_external_impulse", "system_boundary"), required_prompt_terms=("系统", "动量", "外力")),
        "像统计一组服务的总流量：先定边界，再看边界外是否有净输入。", DOMAIN_PROVENANCE,
    ),
}


GRAPH_EDGES: tuple[tuple[str, str], ...] = (
    (P1, P2),
    (P2, P3),
    (P3, P4),
    (P1, P5),
    (P2, P6),
    (P6, P7),
)


RESOURCE_SEEDS: dict[str, ResourceSeed] = {
    P1: ResourceSeed("力来自物体之间的相互作用；先说明谁对谁施力。", "书放在桌面上，地球对书施重力，桌面对书施支持力。", "一本书静止在水平桌面上。", ("选书作为受力对象。", "列出地球和桌面对书的相互作用。"), "书同时受向下重力和向上支持力；静止只说明合力为零。", "逐个写出施力物体、受力物体和力的方向。", "只判断“有 / 没有相互作用力”，再说谁对谁。"),
    P2: ResourceSeed("合力决定加速度；相同合力下，质量越大，加速度越小。", "同样推力作用在空车和满载车上，空车的加速度更大。", "同一恒定合力分别作用在 2 kg 与 4 kg 的小车上。", ("两车所受合力相同。", "按 a=F/m 比较质量带来的响应。"), "4 kg 小车的加速度是 2 kg 小车的一半；这不直接比较两车当前速度。", "先比较合力和质量，再判断加速度，不用速度大小代替。", "只写“更大 / 更小 / 相同”，再补一句 F、m、a 的关系。"),
    P3: ResourceSeed("合力为零时加速度为零，物体可保持静止或匀速直线运动。", "滑块在近似无摩擦冰面上不受合力时，会继续匀速滑行。", "一辆车以不变速率沿圆弯道行驶。", ("速率虽不变，但速度方向持续变化。", "速度矢量变化意味着存在加速度和指向转弯内侧的合力。"), "车辆不是零加速度；方向变化同样属于运动状态改变。", "判断速度矢量有没有变化，再判断合力是否为零。", "只写“零 / 非零”，再说明速度的哪个部分改变。"),
    P4: ResourceSeed("受力分析先固定一个物体，只保留实际作用在它上面的外力。", "画木块受力时保留重力、支持力和摩擦力，不画木块对斜面的力。", "木块静止在粗糙斜面上。", ("选木块作为唯一受力对象。", "列出地球、斜面实际施加在木块上的力。"), "木块受重力、支持力及可能的静摩擦力；木块对斜面的力属于斜面。", "用文字列力即可：每个力都写来源、对象和方向；当前不画图。", "只判断某个力“属于 / 不属于”目标物体的受力列表。"),
    P5: ResourceSeed("作用反作用力大小相等、方向相反，但分别作用在两个不同物体上。", "卡车撞小车时，两车互相施加的碰撞力一样大；加速度不同来自质量不同。", "人向后蹬地，地面对人施加向前的力。", ("人对地施加向后的力。", "地面对人施加大小相等、方向相反的向前力。"), "这对力作用在地面和人两个不同物体上，不会在人的受力图中互相抵消。", "分别写两条“谁对谁施力”，再比较大小和方向。", "只写“相等 / 不相等”，再指出两个受力物体。"),
    P6: ResourceSeed("冲量同时取决于力和作用时间，并等于动量变化。", "同样平均力作用 3 秒比作用 1 秒带来三倍冲量和动量变化。", "平均合力 10 N 作用于小车 2 s。", ("冲量为力对时间的累积。", "用 I=FΔt 得到 20 N·s，并对应 Δp。"), "小车动量改变 20 kg·m/s；若质量未知，不能据此唯一确定速度变化。", "同时使用力、作用时间和动量变化作答。", "只写“更大 / 更小 / 相同”，再补一句力乘时间。"),
    P7: ResourceSeed("动量守恒先选系统；系统在过程中的合外力冲量近似为零时，总动量守恒。", "两车短时碰撞，若把两车作为整体且外界冲量可忽略，碰撞前后总动量相同。", "两辆小车在水平轨道上短时碰撞并粘在一起。", ("选择两车整体为系统。", "碰撞内力成对出现；过程很短时外界水平冲量近似可忽略。"), "两车系统总动量守恒，但机械能不一定守恒；单独一辆车受到另一辆车的冲量。", "先写系统边界，再判断合外力冲量，最后比较总动量。", "只写“守恒 / 不守恒”，再补系统与外界冲量依据。"),
}


PACK = DomainKnowledgePack(
    contract_version="darya_domain_knowledge_pack_v2",
    domain_id=DOMAIN_ID,
    title=DOMAIN_TITLE,
    scope=DOMAIN_SCOPE,
    provenance=DOMAIN_PROVENANCE,
    nodes=NODES,
    edges=GRAPH_EDGES,
    default_node_id=P2,
    resource_seeds=RESOURCE_SEEDS,
    resolver_signals={
        "基础物理": 6, "经典力学": 7, "牛顿定律": 8, "动量守恒": 9,
        "冲量": 7, "动量": 5, "受力分析": 8, "受力图": 7,
        "作用反作用": 8, "碰撞": 4, "撞": 4, "合力": 4, "加速度": 4,
        "momentum": 6, "impulse": 7, "newton": 7, "free body": 8,
        "system boundary": 5, "系统边界": 5,
        # Entry kinematics of the same mechanics scope: constant-acceleration
        # velocity questions must resolve to this installed pack, not fall to
        # open-topic web grounding.
        "匀变速": 5, "匀加速": 5, "匀减速": 5, "运动学": 5,
        "初速度": 3, "末速度": 3, "kinematics": 6,
        # Everyday kinematics wording used by learners mid-dialogue; low
        # weights so no single generic word can match the pack alone.
        "初速": 3, "末速": 3, "减速": 3, "加速": 3, "刹车": 2, "制动": 2,
    },
    goal_rules=(
        GoalResolutionRule(r"动量守恒|conservation\s+of\s+momentum", P7, 220),
        GoalResolutionRule(r"冲量|动量变化|impulse", P6, 210),
        GoalResolutionRule(r"作用.{0,3}反作用|第三定律|action.{0,3}reaction|third\s+law", P5, 205),
        GoalResolutionRule(r"受力分析|受力图|自由体|free.?body|\bFBD\b", P4, 200),
        GoalResolutionRule(r"牛顿.{0,4}定律|惯性|newton", P3, 190),
        GoalResolutionRule(r"合力|加速度|F\s*=\s*ma|net\s+force", P2, 180),
        GoalResolutionRule(r"相互作用|力的概念|force\s+concept", P1, 170),
    ),
    review_expectations=(
        "Force statements identify the system or receiving object.",
        "Acceleration is not replaced by speed, and net force is distinguished from individual forces.",
        "Action-reaction forces are assigned to different bodies.",
        "Momentum conservation claims state the system boundary and external-impulse condition.",
    ),
    stable_source_references=(
        "openstax-university-physics-v1-5.3",
        "openstax-university-physics-v1-5.5",
        "openstax-university-physics-v1-9.2",
        "openstax-university-physics-v1-9.3",
    ),
    source_catalog=SOURCE_CATALOG,
    claim_source_map=CLAIM_SOURCE_MAP,
    memory_context_terms=("物理", "力学", "工程", "physics", "mechanics", "engineering"),
)


def get_node(node_id: str) -> KnowledgeNode:
    return PACK.get_node(node_id)


def children(node_id: str) -> list[str]:
    return PACK.children(node_id)


def ancestors(node_id: str) -> set[str]:
    return PACK.ancestors(node_id)


def select_goal_node(text: str) -> str:
    return PACK.select_goal_node(text)


def graph_contract() -> dict[str, Any]:
    return PACK.graph_contract()


def validate_domain_graph() -> list[str]:
    issues = PACK.validate()
    if len(NODES) < 6 or len(NODES) > 8:
        issues.append("physics_graph_requires_six_to_eight_nodes")
    return issues


assert not validate_domain_graph(), validate_domain_graph()
