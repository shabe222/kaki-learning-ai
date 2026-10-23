from __future__ import annotations
from enum import Enum

class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value

class NodeType(StrEnum):
    FOUNDATION="foundation"; CONCEPT="concept"; PROCEDURE="procedure"; SKILL="skill"
    MISCONCEPTION="misconception"; APPLICATION="application"; INTEGRATION="integration"; ASSESSMENT="assessment"

class NodeStatus(StrEnum):
    PLANNED="planned"; READY="ready"; GENERATING="generating"; GENERATED="generated"
    LEARNING="learning"; COMPLETED="completed"; NEEDS_REVIEW="needs_review"; FAILED="failed"; SUPERSEDED="superseded"

class EdgeType(StrEnum):
    PREREQUISITE="prerequisite"; SUPPORTS="supports"; EXTENDS="extends"; CONTRASTS="contrasts"; APPLIES="applies"

class ResourceType(StrEnum):
    HANDOUT="HANDOUT"; QUESTION="QUESTION"; CODE="CODE"; VIDEO="VIDEO"; MIND_MAP="MIND_MAP"
    INTERACTIVE_CLASS="INTERACTIVE_CLASS"; PPT="PPT"; ADVENTURE="ADVENTURE"

class ResourcePlanStatus(StrEnum):
    PLANNED="planned"; GENERATING="generating"; GENERATED="generated"; FAILED="failed"; SUPERSEDED="superseded"

class ResourceTrigger(StrEnum):
    USER_OPENS_NODE="user_opens_node"; USER_STARTS_LEARNING="user_starts_learning"
    TUTOR_REQUESTS_RESOURCE="tutor_requests_resource"; PATH_DIRECTOR_SELECTS_NODE="path_director_selects_node"
    MANUAL_GENERATE="manual_generate"

class UpdateMode(StrEnum):
    CREATE="CREATE"; EXTEND="EXTEND"; REVISE="REVISE"; REPLACE_SOURCE="REPLACE_SOURCE"; MERGE="MERGE"

class Severity(StrEnum):
    ERROR="error"; REVIEW_REQUIRED="review_required"; WARNING="warning"; INFO="info"

class GenerationMode(StrEnum):
    MODEL="model"; REPAIR="repair"; FALLBACK="fallback"
