"""Private subprocess entry point: receives input only, never an evaluation case."""

import contextlib
import importlib
import json
import os
import sys

from nodal_hindsight.schemas import CandidateResponse, RunContext


def main():
    request = json.load(sys.stdin)
    try:
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            module, attr = request["callable"].split(":", 1)
            function = getattr(importlib.import_module(module), attr)
            response = function(request["input"], RunContext(**request["context"]))
            if isinstance(response, CandidateResponse):
                response = response.model_dump()
            elif isinstance(response, dict) and "output" not in response:
                response = {"output": response}
            response = CandidateResponse.model_validate(response).model_dump(mode="json")
        print(json.dumps({"status": "success", "response": response}, allow_nan=False))
    except Exception:
        print(json.dumps({"status": "error"}))


if __name__ == "__main__":
    main()
