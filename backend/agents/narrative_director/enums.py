from __future__ import annotations

from enum import Enum


class Domain(str, Enum):
    MATH = "math"
    PROGRAMMING = "programming"
    LANGUAGE = "language"
    SCIENCE = "science"
    HUMANITIES = "humanities"
    OPEN = "open"


class NarrativeGrammar(str, Enum):
    DISCOVER_RULE = "DISCOVER_RULE"
    REPAIR_MECHANISM = "REPAIR_MECHANISM"
    EXPOSE_ILLUSION = "EXPOSE_ILLUSION"
    COMPLETE_MISSING_LINK = "COMPLETE_MISSING_LINK"
    RECONSTRUCT_CHAIN = "RECONSTRUCT_CHAIN"
    TRANSFER_ACROSS_BORDER = "TRANSFER_ACROSS_BORDER"


class TeachingMode(str, Enum):
    CONCEPT_BUILDING = "concept_building"
    MISCONCEPTION_DISTINCTION = "misconception_distinction"
    TRANSFER_CHALLENGE = "transfer_challenge"
    SCAFFOLDED_PRACTICE = "scaffolded_practice"
    MECHANISM_LINKING = "mechanism_linking"


class InteractionKind(str, Enum):
    EXPLAIN = "explain"
    ORDERING = "ordering"
    STEP_COMPLETION = "step_completion"
    COMPARE = "compare"
    FIND_ERROR = "find_error"
    COUNTEREXAMPLE = "counterexample"
    APPLICATION = "application"
    CODE_TASK = "code_task"
    REWRITE = "rewrite"
    OPEN_RESPONSE = "open_response"
    MULTIPLE_CHOICE = "multiple_choice"


class TaskRole(str, Enum):
    UNDERSTANDING_TRIAL = "understanding_trial"
    PRACTICE_TRIAL = "practice_trial"
    REMEDY = "remedy"
    UNDERSTANDING_INTERLEAVE = "understanding_interleave"
    PRACTICE_INTERLEAVE = "practice_interleave"
    BOSS_UNDERSTAND = "boss_understand"
    BOSS_DISTINGUISH = "boss_distinguish"
    BOSS_TRANSFER = "boss_transfer"


class CompanionRole(str, Enum):
    CO_OBSERVER = "CO_OBSERVER"
    ONE_STEP_DEMONSTRATOR = "ONE_STEP_DEMONSTRATOR"
    CONTRAST_CUE_GIVER = "CONTRAST_CUE_GIVER"
    FAILURE_ONLY_GUIDE = "FAILURE_ONLY_GUIDE"
    QUIET_COMPANION = "QUIET_COMPANION"
    REFLECTION_PARTNER = "REFLECTION_PARTNER"


class OppositionForm(str, Enum):
    UNKNOWN_PATTERN = "UNKNOWN_PATTERN"
    MALFUNCTION = "MALFUNCTION"
    MISLEADING_CLAIM = "MISLEADING_CLAIM"
    MISSING_CONDITION = "MISSING_CONDITION"
    BROKEN_CHAIN = "BROKEN_CHAIN"
    UNFAMILIAR_CONTEXT = "UNFAMILIAR_CONTEXT"


class AdventureState(str, Enum):
    GOAL = "GOAL"
    SCROLL = "SCROLL"
    METHOD = "METHOD"
    TRIAL = "TRIAL"
    SUPPLY = "SUPPLY"
    INTERLEAVE = "INTERLEAVE"
    BOSS = "BOSS"
    TREASURY = "TREASURY"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    REVIEW = "review_required"


class RepairMode(str, Enum):
    NONE = "none"
    STRUCTURAL = "structural"
    CLEAN = "clean"
