"""Registering an OED specification, and comparing it with what CASS reads.

Three questions, and the registry answers them separately because they have
different consequences.

What does the standard say? That is the specification as its owner published
it, stored whole and checksummed, so a later reading cannot drift from the one
a decision was taken against.

What changed between two versions? Section 8 makes adopting OED 5 a decision
against evidence -- fields, property requirements, codes and conditional
requirements compared -- rather than a dependency bump, and this produces that
comparison.

And what does CASS actually read? The platform validates a subset of OED, which
is a legitimate position and a dangerous one to leave unstated: a required field
CASS does not read is a silent gap, and a column CASS reads that the standard
does not define is a local invention. Both are reported against the active
version rather than discovered by an engine.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from cass_oed.schema import SCHEMA, FileKind, fields_for

#: How the ODS specification names each file, against the kinds CASS reads.
SPEC_FILE_BY_KIND: Mapping[FileKind, str] = {
    FileKind.LOCATION: "Loc",
    FileKind.ACCOUNT: "Acc",
    FileKind.REINS_INFO: "ReinsInfo",
    FileKind.REINS_SCOPE: "ReinsScope",
}

#: The status codes OED uses for a property field. "R" is required; the
#: conditional ones are required once something else is stated.
REQUIRED_STATUSES = frozenset({"R"})
CONDITIONAL_STATUSES = frozenset({"CR", "CR1", "CR2", "CR3", "CR4", "CR5", "CR6"})


class StandardError(Exception):
    """Raised when a specification cannot be read as one."""


@dataclasses.dataclass(frozen=True, slots=True)
class SpecField:
    """One field as the standard defines it."""

    name: str
    file_kind: str
    description: str
    data_type: str
    status: str
    default: str
    valid_values: str

    @property
    def required(self) -> bool:
        return self.status.upper() in REQUIRED_STATUSES

    @property
    def conditionally_required(self) -> bool:
        return self.status.upper() in CONDITIONAL_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file": self.file_kind,
            "description": self.description,
            "data_type": self.data_type,
            "status": self.status,
            "required": self.required,
            "conditionally_required": self.conditionally_required,
            "default": self.default,
            "valid_values": self.valid_values,
        }


def read_specification(payload: bytes) -> dict[str, dict[str, SpecField]]:
    """Read an ODS specification into fields by file, keyed by field name."""
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StandardError(
            "The specification is not readable JSON, so nothing can be registered "
            "from it."
        ) from exc

    inputs = document.get("input_fields")
    if not isinstance(inputs, dict):
        raise StandardError(
            "The specification carries no input_fields section, so it does not "
            "describe an OED release."
        )

    by_file: dict[str, dict[str, SpecField]] = {}
    for file_name, fields in inputs.items():
        if not isinstance(fields, dict) or file_name == "null":
            continue
        found: dict[str, SpecField] = {}
        for entry in fields.values():
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("Input Field Name") or "").strip()
            if not name:
                continue
            found[name] = SpecField(
                name=name,
                file_kind=file_name,
                description=str(entry.get("Type & Description") or "").strip(),
                data_type=str(entry.get("Data Type") or "").strip(),
                status=str(entry.get("Property field status") or "").strip(),
                default=str(entry.get("Default") or "").strip(),
                valid_values=str(entry.get("Valid value range") or "").strip(),
            )
        if found:
            by_file[file_name] = found
    if not by_file:
        raise StandardError("The specification defines no fields.")
    return by_file


def summarise(specification: Mapping[str, Mapping[str, SpecField]]) -> dict[str, Any]:
    """Counts by file, for the registry record."""
    return {name: len(fields) for name, fields in sorted(specification.items())}


def compare(
    earlier: Mapping[str, Mapping[str, SpecField]],
    later: Mapping[str, Mapping[str, SpecField]],
) -> dict[str, Any]:
    """What changed between two versions of the standard.

    Reported file by file and field by field. A changed requirement is listed
    separately from a changed type, because they are different kinds of work: a
    type change is a reader change, and a requirement change can invalidate a
    portfolio that validated yesterday.
    """
    files = sorted(set(earlier) | set(later))
    changes: dict[str, Any] = {}
    for file_name in files:
        before = earlier.get(file_name, {})
        after = later.get(file_name, {})
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))

        requirement_changes = []
        type_changes = []
        for name in sorted(set(before) & set(after)):
            was, now = before[name], after[name]
            if was.status != now.status:
                requirement_changes.append(
                    {"field": name, "before": was.status, "after": now.status}
                )
            if was.data_type != now.data_type:
                type_changes.append(
                    {"field": name, "before": was.data_type, "after": now.data_type}
                )

        if added or removed or requirement_changes or type_changes:
            changes[file_name] = {
                "added": [after[name].as_dict() for name in added],
                "removed": [before[name].as_dict() for name in removed],
                "requirement_changes": requirement_changes,
                "type_changes": type_changes,
            }

    return {
        "files": changes,
        "field_count_before": sum(len(fields) for fields in earlier.values()),
        "field_count_after": sum(len(fields) for fields in later.values()),
        "unchanged": not changes,
    }


def coverage(specification: Mapping[str, Mapping[str, SpecField]]) -> dict[str, Any]:
    """What CASS reads of the standard, and what it does not.

    CASS validates a subset of OED on purpose. Stating the subset is what keeps
    it a position rather than an accident: a required field missing from the
    subset is a gap somebody chose, and a column in the subset that the standard
    does not define is a local invention that will not travel.
    """
    report: dict[str, Any] = {}
    for kind in SCHEMA:
        spec_name = SPEC_FILE_BY_KIND.get(kind)
        defined = specification.get(spec_name or "", {})
        read_here = {spec.name for spec in fields_for(kind)}

        missing_required = sorted(
            name
            for name, field in defined.items()
            if field.required and name not in read_here
        )
        not_in_standard = sorted(read_here - set(defined))
        report[str(kind)] = {
            "file": spec_name,
            "fields_defined": len(defined),
            "fields_read": len(read_here),
            "required_fields_not_read": missing_required,
            "fields_not_in_the_standard": not_in_standard,
        }
    return report
