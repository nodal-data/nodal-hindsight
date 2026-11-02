"""Explicit paid-provider smoke test; never runs in normal CI."""

import os

import pytest

from nodal_hindsight.judges import Judge


@pytest.mark.skipif(
    os.environ.get("HINDSIGHT_LIVE_TEST") != "1",
    reason="Set HINDSIGHT_LIVE_TEST=1 and configure provider/model to opt into a paid call",
)
def test_live_anthropic_judge():
    model = os.environ["HINDSIGHT_JUDGE_MODEL"]
    rubric = {
        "id": "smoke",
        "version": "1",
        "criterion": "Does candidate say hello?",
        "field": "message",
        "permitted_input_fields": [],
    }
    judge = Judge(
        {"provider": "anthropic", "model": model, "max_calls": 1, "parameters": {"max_tokens": 128}}
    )
    response = judge({"criterion": rubric["criterion"], "candidate": {"message": "hello"}}, rubric)
    assert type(response["passed"]) is bool
