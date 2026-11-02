import time


def timeout(context, run_context):
    time.sleep(5)
    return {}


def invalid(context, run_context):
    return {"unexpected": True}


def error(context, run_context):
    raise RuntimeError("SENSITIVE CUSTOMER TEXT MUST NOT BE LOGGED")


def assert_inputs(context, run_context):
    assert set(context) == {
        "prior_state",
        "channel",
        "timezone",
        "day_of_week",
        "customer_profile",
        "history",
    }
    assert not hasattr(run_context, "references")
    return {"next_action": "wait", "follow_ups": [], "message": None}
