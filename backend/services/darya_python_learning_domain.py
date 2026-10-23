"""Python scope/closure Domain Knowledge Pack.

This module owns domain meaning, misconceptions, evidence rules, examples, and
prerequisite relationships.  The learning kernel consumes these contracts and
contains no Python-concept branches of its own.
"""
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


DOMAIN_ID = "python_scope_closure_v1"
DOMAIN_TITLE = "Python 名字绑定、作用域与闭包"
DOMAIN_SCOPE = "Python 名字、绑定、局部/词法作用域、闭包读取与重绑，以及紧邻的函数工厂模式。"
DOMAIN_PROVENANCE = {
    "kind": "stable_language_semantics",
    "source": "Python language reference concepts, bounded editorial contract",
    "external_search_required": False,
}
SOURCE_CATALOG = (
    {
        "source_id": "python-reference-execution-model-3.14",
        "title": "Python Language Reference: Execution model",
        "publisher": "Python Software Foundation",
        "version": "Python 3.14.7 documentation",
        "locator": "4.2.1 Binding of names; 4.2.2 Resolution of names",
        "claim_scope": "name binding, local/free variables, lexical resolution, enclosing scopes, nonlocal resolution",
        "url": "https://docs.python.org/3/reference/executionmodel.html#naming-and-binding",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "official documentation page review"},
    },
    {
        "source_id": "python-reference-simple-statements-3.14",
        "title": "Python Language Reference: Simple statements",
        "publisher": "Python Software Foundation",
        "version": "Python 3.14.7 documentation",
        "locator": "7.2 Assignment statements; 7.13 The nonlocal statement",
        "claim_scope": "assignment binding, rebinding, nonlocal declaration and enclosing-scope requirements",
        "url": "https://docs.python.org/3/reference/simple_stmts.html#the-nonlocal-statement",
        "verification_metadata": {"verified_at": "2026-08-27", "method": "official documentation page review"},
    },
)
CLAIM_SOURCE_MAP = {
    "name_binding": ("python-reference-execution-model-3.14",),
    "mutation_vs_rebinding": ("python-reference-execution-model-3.14", "python-reference-simple-statements-3.14"),
    "local_assignment": ("python-reference-execution-model-3.14", "python-reference-simple-statements-3.14"),
    "read_before_initialization": ("python-reference-execution-model-3.14",),
    "lexical_scope": ("python-reference-execution-model-3.14",),
    "closure_environment": ("python-reference-execution-model-3.14",),
    "closure_instance": ("python-reference-execution-model-3.14",),
    "read_vs_rebind": ("python-reference-execution-model-3.14", "python-reference-simple-statements-3.14"),
    "nonlocal_rebinding": ("python-reference-simple-statements-3.14",),
    "function_factory": ("python-reference-execution-model-3.14",),
    "configuration_capture": ("python-reference-execution-model-3.14",),
}


K1 = "python:names_binding"
K2 = "python:local_global_scope"
K3 = "python:nested_scope"
K4 = "python:closure_reading"
K5 = "python:closure_rebinding_nonlocal"
K6 = "python:mutable_closure_state"
K7 = "python:closure_patterns"


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


_k1_templates, _k1_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先把名字和对象分开。", "`a = b` 这一行里，哪个是名字，哪个是被名字指向的对象？", "只描述绑定关系，不讨论对象内部。", claim_keys=("name_binding",)),
    misconception=CandidateTemplate("targeted_question", "这里混在一起的是名字和对象。", "执行 `items = other` 后，是原列表被改了，还是名字 `items` 改为指向另一个对象？", "答案是名字被重新绑定。", answer_revealed=True, claim_keys=("name_binding",)),
    correct=CandidateTemplate("transfer_question", "名字绑定这一格已经说清了。", "如果两个名字先后指向同一个列表，修改列表内容和重新绑定其中一个名字，结果有什么不同？", claim_keys=("name_binding", "mutation_vs_rebinding")),
    revised=CandidateTemplate("targeted_question", "先只看绑定。", "执行 `items = other` 后，变化的是对象内容，还是名字 `items` 指向的对象？", "先别改列表，只追踪箭头。", claim_keys=("name_binding",)),
)

_k2_templates, _k2_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先判断赋值发生在哪一层。", "函数内写 `score = 1`，且没有 `global` 声明时，这个 `score` 属于哪一层？", "先只看函数体内是否有赋值。", claim_keys=("local_assignment",)),
    misconception=CandidateTemplate("targeted_question", "你已经注意到外面有同名变量。", "解释器会在编译符号表阶段直接判定它是局部名字，所以不会改外层。你能复述吗？", "顺便先学嵌套词法作用域的完整实现。", answer_revealed=True, secondary_concepts=("compiler_symbol_table",), assumed_prerequisites=(K3,), claim_keys=("local_assignment",)),
    correct=CandidateTemplate("transfer_question", "局部绑定规则已经能用了。", "换成 `value = value + 1`：右边读取发生时，本层 `value` 建立了吗？", "把左边的赋值判定和右边的读取顺序连起来。", claim_keys=("local_assignment", "read_before_initialization")),
    revised=CandidateTemplate("targeted_question", "先只补赋值决定本层名字这一格。", "函数内没有 `global` 时，左边的 `value = ...` 会让 `value` 先被判作哪一层的名字？", "先判断归属，再看右边读取。", claim_keys=("local_assignment",)),
)

_k3_templates, _k3_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先画出两层函数。", "内层函数读取一个自己没有赋值的名字时，会先去调用者，还是去词法定义它的外层？", "比较“在哪里定义”和“从哪里调用”。", claim_keys=("lexical_scope",)),
    misconception=CandidateTemplate("targeted_question", "你把调用顺序当成了查找顺序。", "Python 使用动态作用域，所以会先查调用它的函数。这个结论对吗？", "答案是对的。", answer_revealed=True, claim_keys=("dynamic_scope",)),
    correct=CandidateTemplate("transfer_question", "词法外层已经能定位了。", "如果同一个内层函数被传到另一个函数里调用，它读取的外层名字会改为调用者的吗？", claim_keys=("lexical_scope",)),
    revised=CandidateTemplate("targeted_question", "先只看定义位置。", "把内层函数传到别处调用后，它查找外层名字时，依据的是定义位置还是调用位置？", "只选一个，并说明理由。", claim_keys=("lexical_scope",)),
)

_k4_templates, _k4_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先确认什么被函数带走了。", "外层函数已经返回后，返回的内层函数为什么还可能读到外层参数？", "关注定义时保存的词法环境。", claim_keys=("closure_environment",)),
    misconception=CandidateTemplate("targeted_question", "这里容易把闭包误当成调用栈残留。", "外层调用结束后变量仍在栈里，所以闭包能读取它；这就是闭包。", "直接记住这个结论。", answer_revealed=True, claim_keys=("call_stack_retention",)),
    correct=CandidateTemplate("transfer_question", "闭包读取已经能解释了。", "两个由同一工厂函数分别创建的闭包，会共享同一份外层参数吗？为什么？", "根据各自创建时捕获的词法环境判断。", claim_keys=("closure_environment", "closure_instance")),
    revised=CandidateTemplate("targeted_question", "先把调用栈和词法环境分开。", "外层函数返回后，内层函数保留的是一次仍在执行的调用，还是定义时可访问的词法环境？", "再用自己的话解释为什么还能读取。", claim_keys=("closure_environment",)),
)

_k5_templates, _k5_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先不猜是哪一种问题。", "同一个闭包里，`return counter` 和 `counter += 1` 对名字做的事情相同吗？", "只比较一件事：一行只是读取，另一行有没有赋值。", claim_keys=("read_vs_rebind",)),
    misconception=CandidateTemplate("targeted_question_with_analogy", "你已经抓到内层函数能看到外层名字。", "`counter += 1` 执行时，Python 会把 counter 当作哪一层的名字？", "可以顺便对照 global 与 nonlocal 的差别，再判断这里为什么报错。", answer_revealed=True, secondary_concepts=("global",), claim_keys=("nonlocal_rebinding",)),
    correct=CandidateTemplate("transfer_question", "这次你已经把规则说出来了。", "如果内层函数只有 `return counter`，完全不赋值，还需要 `nonlocal` 吗？", "只盯一件事：有没有重新绑定这个名字。", claim_keys=("read_vs_rebind", "nonlocal_rebinding")),
    revised=CandidateTemplate("targeted_question", "先只盯读取与重新绑定。", "先别看完整答案：`counter += 1` 在读取右边旧值之前，Python 会把 `counter` 判作当前函数的局部名字吗？", "先只盯 `counter += 1`：它不只是读取，还发生了赋值。", claim_keys=("nonlocal_rebinding",)),
)

_k6_templates, _k6_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先区分改对象和改名字。", "闭包里执行 `items.append(x)` 与 `items = items + [x]`，哪一个会重新绑定名字？", "一个修改对象，一个给名字赋新值。", claim_keys=("mutation_vs_rebinding",)),
    misconception=CandidateTemplate("targeted_question", "这里把容器修改和名字重绑混了。", "只要状态变了就必须写 `nonlocal`，包括 `append`。", "把它作为统一规则记住。", answer_revealed=True, claim_keys=("all_mutation_needs_nonlocal",)),
    correct=CandidateTemplate("transfer_question", "可变状态的边界已经抓到了。", "如果闭包保存的是字典，修改键值与把整个字典名字换成新字典，`nonlocal` 需求相同吗？", claim_keys=("mutation_vs_rebinding",)),
    revised=CandidateTemplate("targeted_question", "只追踪名字有没有重新指向。", "`items.append(x)` 后，名字 `items` 是否被绑定到一个新对象？这决定要不要 `nonlocal`。", claim_keys=("mutation_vs_rebinding",)),
)

_k7_templates, _k7_revised = _templates(
    low=CandidateTemplate("discriminating_question", "先看闭包在模式里保存什么。", "一个函数工厂返回校验函数时，闭包保存的是哪类配置？", "指出被后续调用重复使用的外层值。", claim_keys=("function_factory",)),
    misconception=CandidateTemplate("targeted_question", "这里把闭包和装饰器语法画了等号。", "只有用了 `@decorator` 语法才算闭包。", "记住装饰器就是闭包的定义。", answer_revealed=True, claim_keys=("decorator_only",)),
    correct=CandidateTemplate("transfer_question", "函数工厂已经能识别。", "不用装饰器语法，怎样用闭包做一个带固定阈值的校验器？", claim_keys=("function_factory", "configuration_capture")),
    revised=CandidateTemplate("targeted_question", "先看结构，不看 `@` 语法。", "只要返回的内层函数继续使用外层配置，它是否已经构成闭包？请用函数工厂说明。", claim_keys=("function_factory",)),
)


NODES: dict[str, KnowledgeNode] = {
    K1: KnowledgeNode(
        K1, "名字与绑定", "区分名字、对象、对象修改与名字重新绑定。", (),
        "能追踪名字在执行过程中指向哪个对象。",
        ("解释名字与对象的区别", "在变体中区分修改对象和重新绑定"),
        ("把变量名字当成对象本身",), "introductory", ("名字", "变量", "绑定", "name", "binding"),
        (
            EvidenceRule(r"(?:名字|变量).{0,20}(?:就是|等于).{0,12}(?:对象|值)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把名字与对象本身混为一谈。", "尚未区分名字绑定与对象本身", .88, "学习者明确把名字说成对象。"),
            EvidenceRule(r"(?:换成|另一个|重新).{0,50}(?:指向|绑定).{0,30}(?:对象|列表)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能在变体里追踪名字重新绑定。", "规则已迁移，仍需后续重复证据", .9, "回答同时区分对象修改与名字重绑。", ("名字是对对象的绑定",)),
            EvidenceRule(r"名字.{0,30}(?:指向|绑定).{0,30}对象|对象.{0,30}(?:修改|内容).{0,30}(?:名字|绑定)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者区分了名字绑定与对象修改。", "规则已能解释，但迁移稳定性尚未观察", .86, "学习者给出了绑定关系。", ("名字与对象不是同一件事",)),
        ), _k1_templates, _k1_revised, ReviewerPolicy(("name_binding", "mutation_vs_rebinding"), required_prompt_terms=("名字", "对象")),
        "借你熟悉的代码参数来看：参数名是入口，实际对象才是被传入的东西。",
    ),
    K2: KnowledgeNode(
        K2, "局部与全局作用域", "理解函数体内赋值如何决定名字归属。", (K1,),
        "能先判断名字属于哪一层，再解释读取和赋值结果。",
        ("解释函数内赋值的局部绑定", "迁移到读取旧值的变体"),
        ("外面有同名变量就会自动修改外层", "把可见性等同于可赋值性"), "basic", ("作用域", "局部", "全局", "scope", "local", "global"),
        (
            EvidenceRule(r"(?:换成|另一个|同样|一样).{0,90}(?:左边|赋值).{0,50}(?:局部|当前函数).{0,80}(?:右边|读取|还没|未).*", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者把局部绑定规则迁移到读取旧值的变体。", "前置规则已达到可继续使用的证据水平", .92, "学习者解释了左侧绑定与右侧读取的顺序。", ("函数内赋值建立局部名字", "读取发生时局部值可能尚未建立")),
            EvidenceRule(r"(?:函数|函数体|内部).{0,45}(?:赋值|给\s*\w+\s*=).{0,55}(?:局部|当前函数)|(?:没有|不写)\s*global.{0,60}(?:局部|当前函数)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者指出函数内赋值默认建立当前函数的局部名字。", "规则已能解释，但还需要变体证据", .88, "学习者说明了赋值与名字归属的关系。", ("赋值会影响名字的作用域判定",)),
            EvidenceRule(r"(?:赋值|给.{0,16}赋值).{0,35}(?:直接|默认|自动).{0,30}(?:全局|外层)|(?:外面|全局).{0,25}同名.{0,35}(?:改|修改)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者认为函数内赋值会因为外层有同名变量而直接修改外层。", "函数内赋值的局部绑定规则尚未稳定", .91, "学习者明确把同名可见性当成外层重绑。", alternatives=("不清楚局部绑定", "混淆读取与赋值")),
        ), _k2_templates, _k2_revised, ReviewerPolicy(("local_assignment", "read_before_initialization"), required_prompt_terms=("哪一层", "局部", "函数")),
        "借你熟悉的 FastAPI 处理函数来看：函数参数和函数体内赋值先属于这次调用自己的局部空间。",
    ),
    K3: KnowledgeNode(
        K3, "嵌套词法作用域", "依据函数定义位置定位词法外层，而不是依据调用者。", (K2,),
        "能在嵌套与跨处调用时稳定判断名字查找路径。",
        ("解释词法作用域", "判断函数被传递后名字查找是否变化"),
        ("名字先去调用者查找",), "basic", ("嵌套", "词法", "定义位置", "nested", "lexical"),
        (
            EvidenceRule(r"(?:传到|换到|别处).{0,60}(?:定义位置|词法外层).{0,50}(?:不变|查找)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能把词法作用域迁移到函数被传递后调用的情形。", "词法作用域已达到可继续使用的证据水平", .91, "学习者用定义位置而非调用位置解释变体。", ("名字查找依据词法定义结构",)),
            EvidenceRule(r"(?:定义位置|词法).{0,60}(?:外层|查找|作用域)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者依据定义位置说明了嵌套名字查找。", "规则已能解释，但迁移稳定性尚未观察", .86, "学习者明确使用词法定义位置。", ("内层函数的外层由定义结构决定",)),
            EvidenceRule(r"(?:调用者|调用它的函数|从哪里调用).{0,45}(?:先查|决定|外层)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把调用位置误当成词法名字查找路径。", "尚未区分词法作用域与调用顺序", .9, "学习者明确选择调用者作为外层。", blocking_prerequisite=K2),
        ), _k3_templates, _k3_revised, ReviewerPolicy(("lexical_scope",), required_prompt_terms=("定义", "调用", "外层")),
        "像 FastAPI 把依赖函数传给框架：函数被框架调用，不会因此改写它定义时的词法外层。",
    ),
    K4: KnowledgeNode(
        K4, "闭包读取", "理解内层函数如何保留并读取定义时可访问的词法环境。", (K3,),
        "能解释外层调用结束后内层函数仍可读取外层名字。",
        ("解释词法环境的保留", "区分不同闭包实例"),
        ("闭包依赖尚未结束的调用栈",), "intermediate", ("闭包", "捕获", "读取", "closure", "capture"),
        (
            EvidenceRule(r"(?:两个|分别|每次).{0,65}(?:闭包|内层函数).{0,55}(?:各自|不同).{0,40}(?:环境|参数|值)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能区分分别创建的闭包实例及其词法环境。", "闭包读取已达到可继续使用的证据水平", .92, "学习者把规则迁移到多个闭包实例。", ("每次创建可形成不同的闭包环境",)),
            EvidenceRule(r"(?:外层函数|外层调用).{0,40}(?:结束|返回).{0,70}(?:词法环境|捕获|保存|记住).{0,45}(?:名字|参数|值)|(?:闭包|内层函数).{0,35}(?:捕获|保存|记住).{0,40}(?:词法|外层)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者用定义时的词法环境解释了闭包读取。", "闭包读取已能解释，但不同实例的迁移尚未观察", .89, "回答指向词法环境而非调用栈残留。", ("闭包保留定义时可访问的词法环境",)),
            EvidenceRule(r"(?:栈|调用栈).{0,45}(?:还在|没有结束|保留).{0,35}(?:所以|因此).{0,35}(?:闭包|读取)", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把闭包解释为调用栈仍未结束。", "尚未区分词法环境与调用栈生命周期", .9, "学习者明确依赖调用栈残留解释闭包。", blocking_prerequisite=K3),
        ), _k4_templates, _k4_revised, ReviewerPolicy(("closure_environment", "closure_instance"), required_prompt_terms=("外层", "环境", "定义")),
        "FastAPI 的依赖工厂可以返回一个仍记得配置参数的函数；记住的是创建时的环境，不是一次请求永远没结束。",
    ),
    K5: KnowledgeNode(
        K5, "闭包重绑与 nonlocal", "区分读取外层名字与重新绑定外层名字。", (K4,),
        "能解释何时需要 nonlocal，并迁移到只读变体。",
        ("解释赋值触发当前层绑定", "回答只读变体", "重复或迁移成功"),
        ("能读取就能直接重绑", "所有闭包都需要 nonlocal"), "intermediate", ("nonlocal", "重绑", "UnboundLocalError", "counter", "rebind"),
        (
            EvidenceRule(r"(?:只读|不赋值|没有赋值|return\s+counter).{0,80}(?:不需要|不用)\s*nonlocal|(?:不用|不需要)\s*nonlocal.{0,80}(?:只读|不赋值|return\s+counter)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者把 nonlocal 规则迁移到只读闭包。", "重绑规则已达到可继续使用的证据水平", .93, "学习者明确以是否重新绑定作为判断依据。", ("只读外层名字不需要 nonlocal", "重新绑定外层名字需要 nonlocal")),
            EvidenceRule(r"nonlocal.{0,90}(?:赋值|重新绑定|局部)|(?:赋值|重新绑定).{0,90}nonlocal|(?:判作|当作|视为).{0,25}局部.{0,75}(?:读取|初始化|UnboundLocalError)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者解释了赋值、局部绑定与 nonlocal 的关系。", "规则已能解释，但只读变体尚未观察", .88, "学习者给出规则和错误发生顺序。", ("赋值会影响名字的作用域判定", "nonlocal 用于声明重新绑定外层名字")),
            EvidenceRule(r"(?:不需要|不用)\s*nonlocal|能(?:看到|访问).{0,35}(?:所以|就).{0,25}(?:改|修改|赋值)|直接(?:修改|改变).{0,20}外层|自动.{0,15}(?:改|修改).{0,15}外层", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者明确把能读取外层名字当成能直接重新绑定外层名字。", "把名字可见性与外层名字的重新绑定混为一谈", .91, "学习者明确说能访问外层变量，因此无需 nonlocal。", ("内层函数可以读取词法外层中的名字",), ("局部绑定规则不清", "只读与赋值混淆"), K2),
        ), _k5_templates, _k5_revised, ReviewerPolicy(("read_vs_rebind", "nonlocal_rebinding"), required_prompt_terms=("counter", "赋值", "读取")),
        "借你熟悉的 FastAPI 依赖工厂来想：内层函数能读到工厂参数，但“读到”和“重新绑定”仍是两件事。",
    ),
    K6: KnowledgeNode(
        K6, "可变闭包状态", "区分修改捕获对象与重新绑定捕获名字。", (K5,),
        "能在列表、字典等状态闭包中判断 nonlocal 是否必要。",
        ("区分 mutation 与 rebinding", "迁移到另一种容器"),
        ("状态变化一律需要 nonlocal",), "intermediate", ("可变", "状态", "append", "字典", "mutable state"),
        (
            EvidenceRule(r"(?:字典|列表|容器).{0,70}(?:修改|键值|append).{0,60}(?:不需要|不用)\s*nonlocal.{0,80}(?:换|重新绑定|新对象)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者把 mutation/rebinding 区分迁移到另一种容器。", "可变闭包状态已达到可继续使用的证据水平", .92, "回答覆盖对象修改与名字重绑两种操作。", ("修改对象不等于重新绑定名字",)),
            EvidenceRule(r"append|修改.{0,25}(?:对象|内容).{0,70}(?:重新绑定|新对象|nonlocal)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者区分了修改对象与重新绑定名字。", "规则已能解释，但容器变体尚未观察", .87, "学习者追踪了名字是否指向新对象。", ("mutation 不自动要求 nonlocal",)),
            EvidenceRule(r"(?:状态|内容).{0,25}(?:变|修改).{0,35}(?:都|就|必须).{0,20}nonlocal|append.{0,35}(?:必须|需要)\s*nonlocal", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者认为任何捕获状态变化都要求 nonlocal。", "尚未区分对象修改与名字重新绑定", .9, "学习者把 mutation 等同于 rebinding。", blocking_prerequisite=K1),
        ), _k6_templates, _k6_revised, ReviewerPolicy(("mutation_vs_rebinding",), required_prompt_terms=("append", "名字", "对象")),
        "把它想成请求处理中修改一个已拿到的配置字典，和把配置变量换成另一份字典：两件事的绑定行为不同。",
    ),
    K7: KnowledgeNode(
        K7, "实用闭包模式", "把闭包用于函数工厂、配置捕获和轻量封装。", (K4,),
        "能识别并写出一个不依赖装饰器语法的实用闭包模式。",
        ("解释函数工厂保存的配置", "迁移到阈值校验器"),
        ("只有装饰器才是闭包",), "intermediate", ("函数工厂", "装饰器", "配置", "factory", "decorator", "pattern"),
        (
            EvidenceRule(r"(?:阈值|配置).{0,45}(?:外层|工厂).{0,55}(?:内层|返回).{0,50}(?:使用|校验)", "correct", "TRANSFER_EXPLANATION", "learner_transfer_answer", "学习者能把函数工厂迁移到固定阈值校验器。", "实用闭包模式已达到可继续使用的证据水平", .91, "回答说明了外层配置如何被返回函数复用。", ("闭包可保存函数工厂配置",)),
            EvidenceRule(r"(?:函数工厂|返回.{0,20}函数).{0,70}(?:保存|捕获|使用).{0,40}(?:配置|参数|外层)", "correct", "EXPLAINED_RULE", "learner_explanation", "学习者解释了函数工厂中的配置捕获。", "模式已能解释，但迁移实现尚未观察", .86, "学习者指出返回函数继续使用外层配置。", ("闭包不依赖装饰器语法",)),
            EvidenceRule(r"(?:只有|必须).{0,25}(?:装饰器|@).{0,30}(?:才是|才算).{0,20}闭包", "incorrect", "EXPLICIT_MISCONCEPTION", "learner_answer", "学习者把闭包限定为装饰器语法。", "尚未识别函数工厂也是闭包模式", .9, "学习者把语法形式当成闭包定义。", blocking_prerequisite=K4),
        ), _k7_templates, _k7_revised, ReviewerPolicy(("function_factory", "configuration_capture"), required_prompt_terms=("函数", "外层", "配置")),
        "FastAPI 的依赖工厂本身就是很贴近的例子：外层先接收配置，再返回真正执行的内层函数。",
    ),
}


GRAPH_EDGES: tuple[tuple[str, str], ...] = (
    (K1, K2),
    (K2, K3),
    (K3, K4),
    (K4, K5),
    (K5, K6),
    (K4, K7),
)


RESOURCE_SEEDS: dict[str, ResourceSeed] = {
    K1: ResourceSeed(
        "名字像标签，对象才是实际的东西。换标签指向，不等于把原对象内部改掉。",
        "`a = items` 后，`a` 与 `items` 可以指向同一列表；`a = other` 只改变 `a` 的绑定。",
        "先执行 `items = [1]`、`alias = items`，再执行 `alias = [2]`。",
        ("前两行让两个名字指向同一个列表对象。", "最后一行创建新列表，并只重新绑定 `alias`。"),
        "`items` 仍指向 `[1]`，`alias` 指向 `[2]`；原列表没有被最后一行修改。",
        "先给出判断，再用一句话说明名字、对象或绑定发生了什么。",
        "只写二选一判断，再补一句绑定关系。",
    ),
    K2: ResourceSeed(
        "函数体里出现赋值时，这个名字默认先算当前函数自己的局部名字，除非明确声明。",
        "函数内写 `score = score + 1` 时，左侧赋值让 `score` 成为局部名字，右侧读取可能发生在局部值建立之前。",
        "外层有 `score = 10`，函数内执行 `score = score + 1`，且没有 `global`。",
        ("函数体内的赋值让 `score` 被判为当前函数的局部名字。", "右侧先读取局部 `score`，但它还没有值。"),
        "运行会得到 `UnboundLocalError`，外层 `score` 不会被自动修改。",
        "先判断名字属于哪一层，再说明右侧读取会发生什么。",
        "只写“局部 / 全局”，再补一句赋值依据。",
    ),
    K3: ResourceSeed(
        "内层函数去哪里找外层名字，看它定义在哪里，不看后来从哪里调用。",
        "把一个内层函数传给另一个调用者，并不会改写它定义时的词法外层。",
        "`inner` 定义在 `outer` 内，随后被传给 `runner` 调用。",
        ("先按定义结构确定 `inner` 的词法外层是 `outer`。", "调用者 `runner` 只负责调用，不会成为新的词法外层。"),
        "`inner` 查找外层名字时仍沿定义位置回到 `outer`。",
        "先指出定义位置与调用位置，再给出名字查找路径。",
        "只选“定义位置 / 调用位置”，再补一句理由。",
    ),
    K4: ResourceSeed(
        "闭包带走的是定义时可访问的词法环境，不是一段永远没结束的调用栈。",
        "工厂函数返回读取 `prefix` 的函数后，外层调用结束，返回函数仍可读取创建时的 `prefix`。",
        "`make_prefix('A')` 返回一个会读取 `prefix` 的内层函数。",
        ("创建内层函数时，它关联了可访问的词法环境。", "外层调用返回后，返回函数仍保留对该环境的访问。"),
        "以后调用返回函数仍能读到 `'A'`；原因不是调用栈一直没结束。",
        "先判断保留的是调用栈还是词法环境，再解释读取结果。",
        "只选“调用栈 / 词法环境”，再补一句理由。",
    ),
    K5: ResourceSeed(
        "读到外层名字和重新绑定外层名字是两件事；重新绑定才需要 `nonlocal`。",
        "`return counter` 只读取；`counter += 1` 同时包含读取与赋值，因此会触发重新绑定判断。",
        "闭包里先看 `return counter`，再看 `counter += 1`。",
        ("第一行只读取词法外层中的名字，不建立新的局部绑定。", "第二行包含赋值；没有 `nonlocal` 时，`counter` 会被当作当前函数的局部名字。"),
        "只读版本不需要 `nonlocal`；重绑版本需要，否则读取旧值时会报错。",
        "先给出判断，再用一句话说明读取与重新绑定的差别。",
        "只写“需要 / 不需要”，再补一句理由。",
    ),
    K6: ResourceSeed(
        "修改名字指向的对象，不等于让这个名字改指向另一个对象。",
        "`items.append(x)` 修改列表；`items = items + [x]` 让名字指向新列表。",
        "闭包分别执行 `items.append(x)` 与 `items = items + [x]`。",
        ("`append` 使用原绑定找到列表，再修改列表内容。", "赋值表达式创建新列表，并尝试重新绑定 `items`。"),
        "前者通常不需要 `nonlocal`，后者若要改外层绑定则需要。",
        "分别判断对象修改和名字重绑，再说明 `nonlocal` 是否必要。",
        "只写“修改对象 / 重绑名字”，再补一句理由。",
    ),
    K7: ResourceSeed(
        "只要返回的内层函数继续使用外层配置，就已经在使用闭包，不要求装饰器语法。",
        "`make_checker(limit)` 返回使用 `limit` 的 `check(value)`，即使没有 `@` 语法也构成闭包。",
        "`make_checker(limit)` 返回 `check(value)`，后者比较 `value >= limit`。",
        ("外层调用为本次工厂创建确定一个 `limit`。", "返回的 `check` 在后续调用中继续读取这份配置。"),
        "这是函数工厂形式的闭包，不依赖装饰器语法。",
        "指出外层保存的配置，以及返回函数怎样继续使用它。",
        "只判断“是 / 不是闭包”，再补一句结构依据。",
    ),
}


PACK = DomainKnowledgePack(
    contract_version="darya_domain_knowledge_pack_v2",
    domain_id=DOMAIN_ID,
    title=DOMAIN_TITLE,
    scope=DOMAIN_SCOPE,
    provenance=DOMAIN_PROVENANCE,
    nodes=NODES,
    edges=GRAPH_EDGES,
    default_node_id=K5,
    resource_seeds=RESOURCE_SEEDS,
    resolver_signals={
        "python": 5, "counter": 5, "nonlocal": 8, "unboundlocalerror": 8, "闭包": 7,
        "名字绑定": 7, "词法作用域": 7, "局部作用域": 6, "函数工厂": 5,
        "global": 3, "decorator": 3, "scope": 5, "作用域": 5,
    },
    goal_rules=(
        GoalResolutionRule(
            r"闭包(?!.*(?:捕获|读取|词法环境|函数工厂|装饰器|可变状态|append))",
            K5,
            200,
        ),
    ),
    review_expectations=(
        "Distinguish names from objects and mutation from rebinding.",
        "Use lexical definition structure rather than dynamic caller scope.",
        "Do not claim all closure access requires nonlocal.",
    ),
    stable_source_references=(
        "python-reference-execution-model-3.14",
        "python-reference-simple-statements-3.14",
    ),
    source_catalog=SOURCE_CATALOG,
    claim_source_map=CLAIM_SOURCE_MAP,
    memory_context_terms=("python", "fastapi", "后端", "开发", "代码"),
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
    if len(NODES) < 5:
        issues.append("graph_requires_at_least_five_nodes")
    return issues


assert not validate_domain_graph(), validate_domain_graph()
