"""DARYA's blackboard: geometry solved on the server, chalk drawn in the browser.

The contract between the two halves is a list of operations::

    {"kind": "add", "element": {...}}      # write this element
    {"kind": "update", "element": {...}}   # rewrite it in place
    {"kind": "remove", "id": "..."}        # wipe it
    {"kind": "clear"}                      # wipe the board
    {"kind": "pointer", "bbox": {...}}     # flash a region, leave nothing behind

Nothing here calls a model. ``script.compile_intent`` turns the semantic scene
DARYA already produces into those operations, which is why switching from text
to blackboard costs no extra inference.
"""
from services.blackboard.figures import FigureError, draw_figure, validate_figure
from services.blackboard.model import BOARD, CHALK, Board, build_script, diff_ops, resolve_color, seed_for
from services.blackboard.script import (
    compile_intent,
    compile_script,
    digest_of,
    iter_elements,
    measure_label_consistency,
    outside_board,
    overlapping_pairs,
    seed_consistency,
    wrap_text,
)

__all__ = [
    "BOARD",
    "CHALK",
    "Board",
    "FigureError",
    "build_script",
    "compile_intent",
    "compile_script",
    "diff_ops",
    "digest_of",
    "draw_figure",
    "iter_elements",
    "measure_label_consistency",
    "outside_board",
    "overlapping_pairs",
    "resolve_color",
    "seed_consistency",
    "seed_for",
    "validate_figure",
    "wrap_text",
]
