"""Load and validate configurable business-rule settings."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .exceptions import ConfigurationError


NO_SHOW_POLICIES = {"no_documentation_required", "review"}
APPOINTMENT_GROUPS = {
    "documentation_required",
    "cancelled",
    "no_show",
    "rescheduled",
}
NOTE_GROUPS = {"signed", "unsigned"}


@dataclass(frozen=True)
class ValidationConfig:
    """Normalized settings used by the deterministic rule evaluator."""

    rules_version: str
    date_tolerance_days: int
    no_show_policy: str
    require_signed_date_for_signed_notes: bool
    appointment_status_groups: dict[str, frozenset[str]]
    note_status_groups: dict[str, frozenset[str]]

    @classmethod
    def from_file(cls, path: str | Path) -> "ValidationConfig":
        config_path = Path(path)
        try:
            with config_path.open(encoding="utf-8") as config_file:
                raw = json.load(config_file)
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Configuration is not valid JSON ({config_path}): {exc.msg}"
            ) from exc

        if not isinstance(raw, dict):
            raise ConfigurationError("Configuration root must be a JSON object.")

        rules_version = raw.get("rules_version")
        if not isinstance(rules_version, str) or not rules_version.strip():
            raise ConfigurationError("rules_version must be a non-empty string.")

        tolerance = raw.get("date_tolerance_days")
        if not isinstance(tolerance, int) or isinstance(tolerance, bool) or tolerance < 0:
            raise ConfigurationError("date_tolerance_days must be a non-negative integer.")

        no_show_policy = raw.get("no_show_policy")
        if not isinstance(no_show_policy, str) or no_show_policy not in NO_SHOW_POLICIES:
            allowed = ", ".join(sorted(NO_SHOW_POLICIES))
            raise ConfigurationError(f"no_show_policy must be one of: {allowed}.")

        require_signed_date = raw.get("require_signed_date_for_signed_notes")
        if not isinstance(require_signed_date, bool):
            raise ConfigurationError(
                "require_signed_date_for_signed_notes must be true or false."
            )

        appointment_groups = _normalize_groups(
            raw.get("appointment_status_groups"),
            APPOINTMENT_GROUPS,
            "appointment_status_groups",
        )
        note_groups = _normalize_groups(
            raw.get("note_status_groups"), NOTE_GROUPS, "note_status_groups"
        )

        return cls(
            rules_version=rules_version.strip(),
            date_tolerance_days=tolerance,
            no_show_policy=no_show_policy,
            require_signed_date_for_signed_notes=require_signed_date,
            appointment_status_groups=appointment_groups,
            note_status_groups=note_groups,
        )

    def appointment_group(self, status: str) -> str | None:
        return _find_group(status, self.appointment_status_groups)

    def note_group(self, status: str) -> str | None:
        return _find_group(status, self.note_status_groups)


def _normalize_groups(
    raw_groups: Any, required_groups: set[str], setting_name: str
) -> dict[str, frozenset[str]]:
    if not isinstance(raw_groups, dict):
        raise ConfigurationError(f"{setting_name} must be a JSON object.")

    missing = required_groups.difference(raw_groups)
    extra = set(raw_groups).difference(required_groups)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unexpected {sorted(extra)}")
        raise ConfigurationError(f"Invalid {setting_name}: {'; '.join(details)}.")

    normalized: dict[str, frozenset[str]] = {}
    seen: dict[str, str] = {}
    for group_name in sorted(required_groups):
        values = raw_groups[group_name]
        if not isinstance(values, list) or not values:
            raise ConfigurationError(
                f"{setting_name}.{group_name} must be a non-empty list."
            )

        normalized_values: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"{setting_name}.{group_name} values must be non-empty strings."
                )
            normalized_value = value.strip().casefold()
            if normalized_value in seen:
                raise ConfigurationError(
                    f"Status {value!r} appears in both {seen[normalized_value]!r} "
                    f"and {group_name!r}."
                )
            seen[normalized_value] = group_name
            normalized_values.add(normalized_value)
        normalized[group_name] = frozenset(normalized_values)

    return normalized


def _find_group(value: str, groups: dict[str, frozenset[str]]) -> str | None:
    normalized = value.strip().casefold()
    for group_name, values in groups.items():
        if normalized in values:
            return group_name
    return None
