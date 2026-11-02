"""Deterministic examples exercising the runner without network calls."""

from nodal_hindsight.schemas import CandidateResponse


def baseline(context, run_context):
    if context["prior_state"] == "unresolved_request":
        output = {
            "next_action": "escalate_to_responder_lead",
            "follow_ups": ["review_with_lead"],
            "message": None,
        }
    elif "tomorrow" in context["history"][-1]["content"].lower():
        output = {"next_action": "wait", "follow_ups": ["retry_contact"], "message": None}
    else:
        output = {
            "next_action": "send_message",
            "follow_ups": ["check_reply"],
            "message": "Would the proposed time work for you?",
        }
    return CandidateResponse(output=output, model="synthetic-model-v1")


def candidate(context, run_context):
    # Intentional regression: closes an unresolved case, to demonstrate comparison gates.
    if context["prior_state"] == "unresolved_request":
        return {"next_action": "close_case", "follow_ups": [], "message": None}
    return baseline(context, run_context)
