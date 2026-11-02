"""Canonical IO, identity, and atomic artifact helpers."""

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml


class HindsightError(ValueError):
    """Safe diagnostic suitable for CLI output."""


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise HindsightError(
            "invalid timestamp: an ISO timestamp with offset is required"
        ) from None


def iso(value):
    return timestamp(value).isoformat().replace("+00:00", "Z")


def reject_constant(value):
    raise ValueError("nonfinite JSON number")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(), parse_constant=reject_constant)
    except (ValueError, OSError):
        raise HindsightError(f"cannot read valid JSON: {path}") from None


def read_jsonl(path, quarantine=None):
    result = []
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, UnicodeError):
        raise HindsightError(f"cannot read JSONL: {path}") from None
    for line, text in enumerate(lines, 1):
        if not text.strip():
            continue
        try:
            item = json.loads(text, parse_constant=reject_constant)
            if not isinstance(item, dict):
                raise ValueError
            result.append(item)
        except ValueError:
            error = {"source": str(path), "line": line, "reason": "invalid_json_object"}
            if quarantine is None:
                raise HindsightError(f"{path}:{line}: invalid JSON object") from None
            quarantine.append(error)
    return result


def config_file(path):
    path = Path(path)
    if path.suffix == ".jsonl":
        return read_jsonl(path)
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raise HindsightError(f"cannot read configuration: {path}") from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_jsonl(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical(v) + "\n" for v in values))


@contextmanager
def artifact_dir(destination, overwrite=False):
    destination = Path(destination)
    if destination.exists() and not overwrite:
        raise HindsightError(f"destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".hindsight-", dir=destination.parent))
    backup = None
    try:
        yield staging
        if destination.exists():
            backup = destination.with_name(destination.name + ".previous-" + staging.name)
            destination.rename(backup)
        try:
            staging.rename(destination)
        except BaseException:
            if backup:
                backup.rename(destination)
            raise
        if backup:
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def protected_split(split, allow_test=False):
    if split not in ("train", "dev", "test", "calibration"):
        raise HindsightError("split must be train, dev, test, or calibration")
    if split == "test" and not allow_test:
        raise HindsightError("test access requires --allow-test")


def markdown_report(title, value):
    return f"# {title}\n\n```json\n{json.dumps(value, indent=2, ensure_ascii=False)}\n```\n"


def project_path(value, path):
    """Resolve a declarative dotted path, with [] expansion for histories."""
    nodes = [value]
    for segment in path.split("."):
        array = segment.endswith("[]")
        key = segment[:-2] if array else segment
        next_nodes = []
        for node in nodes:
            if not isinstance(node, dict) or key not in node:
                raise HindsightError("configured context path is absent")
            item = node[key]
            if array:
                if not isinstance(item, list):
                    raise HindsightError("configured array path is not an array")
                next_nodes.extend(item)
            else:
                next_nodes.append(item)
        nodes = next_nodes
    return nodes
