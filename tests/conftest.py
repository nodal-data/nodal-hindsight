import copy
from pathlib import Path

import pytest

from nodal_hindsight.common import read_jsonl, write_jsonl
from nodal_hindsight.datasets import compile_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture_data(tmp_path):
    decisions = read_jsonl(ROOT / "examples/health_tech/decisions.jsonl")
    events = read_jsonl(ROOT / "examples/health_tech/events.jsonl")
    tasks = read_jsonl(ROOT / "examples/health_tech/tasks.jsonl")

    def build(
        decisions_override=None, events_override=None, tasks_override=None, name="dataset", **kwargs
    ):
        write_jsonl(
            tmp_path / "decisions.jsonl",
            copy.deepcopy(decisions if decisions_override is None else decisions_override),
        )
        write_jsonl(
            tmp_path / "events.jsonl",
            copy.deepcopy(events if events_override is None else events_override),
        )
        write_jsonl(
            tmp_path / "tasks.jsonl",
            copy.deepcopy(tasks if tasks_override is None else tasks_override),
        )
        dest = tmp_path / name
        compile_dataset(
            tmp_path / "decisions.jsonl",
            tmp_path / "events.jsonl",
            tmp_path / "tasks.jsonl",
            "2026-10-01T00:00:00Z",
            dest,
            **kwargs,
        )
        return dest

    return decisions, events, tasks, build
