"""Versioned, fail-closed Desktop to Server data-minimisation contract."""

from __future__ import annotations

from typing import Any, Literal


PUBLISH_CONTRACT_VERSION = "2026-07-30"

FieldPurpose = Literal[
    "assignment",
    "capability_requirement",
    "location",
    "operational_instruction",
    "reference",
    "timing",
]
FieldVisibility = Literal[
    "organiser",
    "participant",
    "public",
    "never_publish",
]
FieldType = Literal[
    "capabilities_list",
    "duration",
    "link",
    "location",
    "number",
    "persons_list",
    "start_end_time",
    "text",
    "time_range",
]


def validate_published_field_value(field_type: str, value: Any) -> bool:
    """Return whether a value matches the bounded wire type declared for it."""

    if field_type in {"text", "link"}:
        return isinstance(value, str) and len(value) <= 10_000
    if field_type in {"number", "duration"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "capabilities_list":
        return (
            isinstance(value, list)
            and len(value) <= 200
            and all(isinstance(item, str) and len(item) <= 128 for item in value)
        )
    if field_type == "persons_list":
        return (
            isinstance(value, list)
            and len(value) <= 500
            and all(
                isinstance(item, dict)
                and set(item) == {"name", "person_id"}
                and isinstance(item["name"], str)
                and len(item["name"]) <= 256
                and isinstance(item["person_id"], int)
                and item["person_id"] > 0
                for item in value
            )
        )
    if field_type == "location":
        return (
            isinstance(value, dict)
            and set(value).issubset({"name", "address"})
            and isinstance(value.get("name"), str)
            and len(value["name"]) <= 512
            and (
                value.get("address") is None
                or (
                    isinstance(value.get("address"), str)
                    and len(value["address"]) <= 1024
                )
            )
        )
    if field_type in {"start_end_time", "time_range"}:
        return (
            isinstance(value, dict)
            and set(value).issubset({"start", "end"})
            and set(value) == {"start", "end"}
            and all(isinstance(value[key], str) and len(value[key]) <= 64 for key in ("start", "end"))
        )
    return False
