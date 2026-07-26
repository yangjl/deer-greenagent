"""Evaluation telemetry, stored separately from DBTL domain records.

Kept out of ``deerflow.persistence.dbtl`` deliberately: a shadow evaluation is
an observation about the classifier, not a research record, and this package
imports no DBTL model — so it has nothing to mutate a cycle with.
"""

from deerflow.persistence.telemetry.evaluations import (
    ClassifierEvaluationConflict,
    ClassifierEvaluationRepository,
)
from deerflow.persistence.telemetry.model import ClassifierEvaluationRow

__all__ = [
    "ClassifierEvaluationRepository",
    "ClassifierEvaluationConflict",
    "ClassifierEvaluationRow",
]
