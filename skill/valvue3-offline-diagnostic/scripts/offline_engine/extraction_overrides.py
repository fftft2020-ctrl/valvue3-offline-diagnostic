from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ProjectConfig


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid extraction overrides JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("extraction overrides schema_version must be 1")
    valves = payload.get("valves")
    if not isinstance(valves, dict):
        raise RuntimeError("extraction overrides valves must be an object")
    return payload


def load_extraction_overrides(
    config: ProjectConfig,
    *,
    valve_tags: set[str],
    field_names: set[str],
) -> dict[str, list[dict[str, Any]]]:
    path = config.project_dir / "experience/extraction_overrides.json"
    if not path.is_file():
        return {}
    payload = _load_payload(path)
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_tag, raw_valve in payload["valves"].items():
        valve_tag = str(raw_tag).strip()
        if valve_tag not in valve_tags:
            raise RuntimeError(
                f"extraction override references an unknown valve: {valve_tag}"
            )
        fields = raw_valve.get("fields") if isinstance(raw_valve, dict) else None
        if not isinstance(fields, dict):
            raise RuntimeError(
                f"extraction override fields must be an object for {valve_tag}"
            )
        for field_name, raw_override in fields.items():
            if field_name not in field_names:
                raise RuntimeError(
                    f"extraction override contains an unknown field: {field_name}"
                )
            if not isinstance(raw_override, dict) or raw_override.get("value") is None:
                raise RuntimeError(
                    f"extraction override requires value for {valve_tag}.{field_name}"
                )
            reason = str(raw_override.get("reason") or "").strip()
            if not reason:
                raise RuntimeError(
                    f"extraction override requires reason for {valve_tag}.{field_name}"
                )
            result.setdefault(valve_tag, []).append(
                {
                    "field": field_name,
                    "value": raw_override["value"],
                    "path": path,
                    "record": f"valves.{valve_tag}.fields.{field_name}",
                    "method": "manual_override",
                    "source_role": "manual_override",
                    "priority": 1000,
                    "candidate_status": "manual_override",
                    "reason": reason,
                    "raw_unit": str(raw_override.get("unit") or ""),
                }
            )
    return result
