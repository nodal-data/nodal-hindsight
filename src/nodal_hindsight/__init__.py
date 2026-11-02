"""Public API for evidence compilation and evaluation."""

from nodal_hindsight.datasets import compile_dataset, export_dataset
from nodal_hindsight.evaluation import compare_evaluations, evaluate_predictions
from nodal_hindsight.ingest import validate
from nodal_hindsight.runners import run_candidate

__version__ = "0.1.0"
__all__ = [
    "validate",
    "compile_dataset",
    "run_candidate",
    "evaluate_predictions",
    "compare_evaluations",
    "export_dataset",
]
