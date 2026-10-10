from __future__ import annotations

import math
import re
from typing import Any, Iterable


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "1.0.0"

ROOT_FIELDS = {
    "schema_version",
    "extractor_version",
    "project_count",
    "valve_count",
    "projects",
}
PROJECT_FIELDS = {"project_id", "valves"}
VALVE_FIELDS = {"valve_id", "legacy", "shared"}
SNAPSHOT_FIELD_ORDER = (
    "rating",
    "algorithm_rating",
    "metrics",
    "image_count",
    "has_specification",
)
SNAPSHOT_FIELDS = set(SNAPSHOT_FIELD_ORDER)
SCALAR_METRICS = (
    "Average Error",
    "Average Hysteresis + Dead Band",
    "Max Friction",
    "Maximum Hysteresis + Dead Band",
    "Mean Friction",
    "Overshoot",
    "Response Time Close",
    "Response Time Open",
    "T63",
    "T86",
    "Td",
)
METRIC_FIELDS = set(SCALAR_METRICS) | {
    "Supply Pressure",
    "step_count",
    "test_types",
}
SUPPLY_PRESSURE_FIELDS = {"avg", "max", "min"}
TEST_TYPES = {"SIG-EXT", "SIG-POS", "SIG-S", "STEP"}
PROJECT_ID_PATTERN = re.compile(r"PROJECT-\d{3}")
VALVE_ID_PATTERN = re.compile(r"VALVE-\d{3}")


class RegressionFixtureError(ValueError):
    """Raised when source summaries or an anonymous fixture violate the contract."""


def _require_exact_fields(
    value: Any,
    allowed: set[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegressionFixtureError(f"{context} must be an object")
    missing = allowed - set(value)
    unknown = set(value) - allowed
    if missing:
        raise RegressionFixtureError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise RegressionFixtureError(
            f"{context} has unsupported fields: {', '.join(sorted(unknown))}"
        )
    return value


def _rating(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 6):
        raise RegressionFixtureError(f"{context} must be an integer from 1 to 5")
    return value


def _number_or_none(value: Any, context: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegressionFixtureError(f"{context} must be a finite number or null")
    if not math.isfinite(float(value)):
        raise RegressionFixtureError(f"{context} must be finite")
    return value


def _normalized_metrics(value: Any, context: str) -> dict[str, Any]:
    metrics = _require_exact_fields(value, METRIC_FIELDS, context)
    normalized: dict[str, Any] = {
        field: _number_or_none(metrics[field], f"{context}.{field}")
        for field in SCALAR_METRICS
    }
    supply = _require_exact_fields(
        metrics["Supply Pressure"],
        SUPPLY_PRESSURE_FIELDS,
        f"{context}.Supply Pressure",
    )
    normalized["Supply Pressure"] = {
        field: _number_or_none(
            supply[field],
            f"{context}.Supply Pressure.{field}",
        )
        for field in ("avg", "max", "min")
    }
    step_count = metrics["step_count"]
    if (
        isinstance(step_count, bool)
        or not isinstance(step_count, int)
        or step_count < 0
    ):
        raise RegressionFixtureError(
            f"{context}.step_count must be a non-negative integer"
        )
    normalized["step_count"] = step_count
    test_types = metrics["test_types"]
    if not isinstance(test_types, list):
        raise RegressionFixtureError(f"{context}.test_types must be a list")
    if any(not isinstance(item, str) or item not in TEST_TYPES for item in test_types):
        raise RegressionFixtureError(
            f"{context}.test_types contains an unsupported test type"
        )
    if len(test_types) != len(set(test_types)):
        raise RegressionFixtureError(f"{context}.test_types must be unique")
    normalized["test_types"] = list(test_types)
    return normalized


def equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            equivalent(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            equivalent(old, new) for old, new in zip(left, right)
        )
    return left == right


def _string_values(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _string_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_values(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def scan_fixture_strings(
    fixture: dict[str, Any],
    *,
    forbidden_tokens: Iterable[str] = (),
) -> list[str]:
    tokens = sorted(
        {token.strip() for token in forbidden_tokens if token.strip()},
        key=len,
        reverse=True,
    )
    errors: list[str] = []
    for path, value in _string_values(fixture):
        if "/" in value or "\\" in value:
            errors.append(f"{path} contains a path-like string")
        folded = value.casefold()
        for token in tokens:
            token_folded = token.casefold()
            if token_folded == folded or (
                len(token_folded) >= 4 and token_folded in folded
            ) or (
                any(ord(char) > 127 for char in token_folded)
                and token_folded in folded
            ):
                errors.append(f"{path} contains a forbidden customer identifier")
                break
    return errors


def validate_fixture(
    fixture: Any,
    *,
    forbidden_tokens: Iterable[str] = (),
) -> None:
    root = _require_exact_fields(fixture, ROOT_FIELDS, "fixture")
    if (
        isinstance(root["schema_version"], bool)
        or not isinstance(root["schema_version"], int)
        or root["schema_version"] != SCHEMA_VERSION
    ):
        raise RegressionFixtureError("fixture has an unsupported schema_version")
    if root["extractor_version"] != EXTRACTOR_VERSION:
        raise RegressionFixtureError("fixture has an unsupported extractor_version")
    projects = root["projects"]
    if not isinstance(projects, list) or not projects:
        raise RegressionFixtureError("fixture.projects must be a non-empty list")
    if isinstance(root["project_count"], bool) or not isinstance(
        root["project_count"], int
    ):
        raise RegressionFixtureError("fixture.project_count must be an integer")
    if root["project_count"] != len(projects):
        raise RegressionFixtureError("fixture.project_count does not match projects")

    valve_count = 0
    for project_index, raw_project in enumerate(projects, start=1):
        context = f"fixture.projects[{project_index - 1}]"
        project = _require_exact_fields(raw_project, PROJECT_FIELDS, context)
        expected_project_id = f"PROJECT-{project_index:03d}"
        if (
            not isinstance(project["project_id"], str)
            or not PROJECT_ID_PATTERN.fullmatch(project["project_id"])
            or project["project_id"] != expected_project_id
        ):
            raise RegressionFixtureError(
                f"{context}.project_id must be {expected_project_id}"
            )
        valves = project["valves"]
        if not isinstance(valves, list) or not valves:
            raise RegressionFixtureError(f"{context}.valves must be a non-empty list")
        valve_count += len(valves)
        for valve_index, raw_valve in enumerate(valves, start=1):
            valve_context = f"{context}.valves[{valve_index - 1}]"
            valve = _require_exact_fields(raw_valve, VALVE_FIELDS, valve_context)
            expected_valve_id = f"VALVE-{valve_index:03d}"
            if (
                not isinstance(valve["valve_id"], str)
                or not VALVE_ID_PATTERN.fullmatch(valve["valve_id"])
                or valve["valve_id"] != expected_valve_id
            ):
                raise RegressionFixtureError(
                    f"{valve_context}.valve_id must be {expected_valve_id}"
                )
            for side in ("legacy", "shared"):
                snapshot_context = f"{valve_context}.{side}"
                snapshot = _require_exact_fields(
                    valve[side], SNAPSHOT_FIELDS, snapshot_context
                )
                _rating(snapshot["rating"], f"{snapshot_context}.rating")
                _rating(
                    snapshot["algorithm_rating"],
                    f"{snapshot_context}.algorithm_rating",
                )
                _normalized_metrics(
                    snapshot["metrics"],
                    f"{snapshot_context}.metrics",
                )
                image_count = snapshot["image_count"]
                if (
                    isinstance(image_count, bool)
                    or not isinstance(image_count, int)
                    or image_count < 0
                ):
                    raise RegressionFixtureError(
                        f"{snapshot_context}.image_count must be a non-negative integer"
                    )
                if not isinstance(snapshot["has_specification"], bool):
                    raise RegressionFixtureError(
                        f"{snapshot_context}.has_specification must be boolean"
                    )
    if isinstance(root["valve_count"], bool) or not isinstance(
        root["valve_count"], int
    ):
        raise RegressionFixtureError("fixture.valve_count must be an integer")
    if root["valve_count"] != valve_count:
        raise RegressionFixtureError("fixture.valve_count does not match valves")
    leaks = scan_fixture_strings(root, forbidden_tokens=forbidden_tokens)
    if leaks:
        raise RegressionFixtureError("; ".join(leaks))


def fixture_regression_errors(fixture: Any) -> list[str]:
    validate_fixture(fixture)
    errors: list[str] = []
    for project in fixture["projects"]:
        for valve in project["valves"]:
            for field in SNAPSHOT_FIELD_ORDER:
                if not equivalent(valve["legacy"][field], valve["shared"][field]):
                    errors.append(
                        f"{project['project_id']}/{valve['valve_id']}: {field} differs"
                    )
    return errors
