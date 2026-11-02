"""Versioned public contracts. Domain-specific schemas are JSON Schema documents."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nodal_hindsight.common import iso


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Policy(Contract):
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    code_version: str = Field(min_length=1)


class ContextProvenance(Contract):
    snapshot_at: str
    decision_time_complete: bool
    history_timestamp_path: str | None = None

    _time = field_validator("snapshot_at")(iso)


class Decision(Contract):
    schema_version: Literal["1.0"] = "1.0"
    record_type: Literal["decision"] = "decision"
    decision_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    decided_at: str
    context: dict
    context_provenance: ContextProvenance
    policy: Policy
    suggestion: dict
    exposure: Literal["shown", "hidden", "unknown"]
    tags: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)
    sampling: dict | None = None
    logging_policy: dict | None = None
    request_metadata: dict | None = None
    response_metadata: dict | None = None
    extensions: dict = Field(default_factory=dict)

    _time = field_validator("decided_at")(iso)


class Event(Contract):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    event_at: str
    observed_at: str
    source: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    _times = field_validator("event_at", "observed_at")(iso)


class HumanAction(Event):
    record_type: Literal["human_action"] = "human_action"
    actor: str | None = None
    kind: Literal[
        "accepted",
        "edited",
        "overridden",
        "independent",
        "rejected",
        "escalated",
        "none",
        "unknown",
    ]
    output: dict = Field(default_factory=dict)
    reason: str | None = None
    attribution: dict | None = None
    observation_complete: bool = True


class Outcome(Event):
    record_type: Literal["outcome"] = "outcome"
    outcome_type: str
    value: Any = None
    attribution: dict = Field(default_factory=dict)


class ReviewDecision(Event):
    record_type: Literal["review"] = "review"
    reviewer: str = Field(min_length=1)
    disposition: Literal["approve", "exclude", "negative"]
    fields: list[str]
    references: dict = Field(default_factory=dict)
    acceptable: dict = Field(default_factory=dict)
    negatives: dict = Field(default_factory=dict)
    reason: str = Field(min_length=1)


class Task(Contract):
    schema_version: Literal["1.0"] = "1.0"
    record_type: Literal["task"] = "task"
    task: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    input_schema: dict
    output_schema: dict
    field_roles: dict[str, Literal["categorical", "set", "ranked_list", "prose"]]
    human_action_semantics: dict = Field(default_factory=dict)
    context_rules: dict = Field(default_factory=dict)
    outcome_policy: dict = Field(default_factory=dict)
    label_policy: dict = Field(default_factory=dict)
    split_policy: dict
    scorers: list[dict]
    constraints: list[dict] = Field(default_factory=list)
    rubrics: list[dict] = Field(default_factory=list)
    composition: dict = Field(default_factory=lambda: {"enabled": False})
    gates: dict = Field(default_factory=dict)
    slices: list[str] = Field(default_factory=list)
    redaction: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)


class Label(Contract):
    tier: Literal["Gold", "Silver", "Bronze", "Weak", "Unknown"]
    eligible: bool
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    subtype: str | None = None


class EvalCase(Contract):
    case_id: str
    version: str
    task: str
    task_version: str
    decision_id: str
    group_id: str
    decided_at: str
    split: Literal["train", "dev", "test", "calibration"]
    input: dict
    references: dict
    acceptable: dict = Field(default_factory=dict)
    negatives: dict = Field(default_factory=dict)
    labels: dict[str, Label]
    exposure: str
    feedback: list[dict]
    provenance: dict
    suggestion: dict
    tags: dict
    rubric_ids: list[str]
    outcome_status: str
    correction_group: str
    review_flags: list[str] = Field(default_factory=list)


class CandidateResponse(Contract):
    output: dict
    usage: dict = Field(default_factory=dict)
    model: str | None = None


class RunContext(Contract):
    case_id: str
    timeout_seconds: float
    deadline: str
    candidate_config: dict = Field(default_factory=dict)


class RunConfig(Contract):
    model: str = "unknown"
    prompt_version: str = "unknown"
    code_version: str = "unknown"
    parameters: dict = Field(default_factory=dict)
    adapter_version: str = "1"
    timeout_seconds: float = Field(default=30, gt=0)
    retries: int = Field(default=0, ge=0)
    max_calls: int = Field(default=1000, ge=0)
    max_cases: int = Field(default=1000, ge=0)
    concurrency: Literal[1] = 1
    cache: bool = False
    cache_dir: str = ".cache/hindsight/candidates"
    retain_output: bool = True
    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    token_upper_bound_per_call: int | None = Field(default=None, gt=0)
    cost_upper_bound_per_call: float | None = Field(default=None, gt=0)
    bounded_adapter: bool = False


class Prediction(Contract):
    case_id: str
    output: dict | None = None
    status: Literal["success", "timeout", "invalid_output", "error", "budget_exhausted"] = "success"
    candidate: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    latency_seconds: float | None = None
    cache_hit: bool = False
    cache_read_seconds: float | None = None
    attempts: list[dict] = Field(default_factory=list)


class MetricResult(Contract):
    metric: str
    version: str = "1"
    field: str
    direction: Literal["higher", "lower"] = "higher"
    value: float | None
    status: Literal["scored", "inapplicable", "error", "insufficient_evidence"]
    eligible: bool
    passed: bool | None = None
    feedback: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class Verdict(Contract):
    case_id: str
    case_version: str
    group_id: str
    valid: bool
    metrics: list[MetricResult]
    score: float | None
    composite_required: bool = False
    gate: Literal["pass", "fail", "insufficient_evidence"]
    constraints: list[dict]
    tags: dict
    exposure: str
    labels: dict
    prediction_status: str
    resources: dict = Field(default_factory=dict)


MODELS = [
    Decision,
    HumanAction,
    Outcome,
    ReviewDecision,
    Task,
    EvalCase,
    Prediction,
    CandidateResponse,
    RunContext,
    RunConfig,
    MetricResult,
    Verdict,
]
