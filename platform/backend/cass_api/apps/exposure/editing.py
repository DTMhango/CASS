"""Reading and correcting a portfolio on the platform.

A portfolio arrived as four files and could only be changed by uploading four
more. A person who spotted one wrong occupancy code had to go back to the
spreadsheet, fix it there, re-export and re-attach -- and whether the file they
uploaded was the file they fixed was a matter of trust.

So the rows are readable here, and correctable here. Three rules shape it.

**A published version never changes.** Section 5 makes reported exposure
immutable, and a run points at a version precisely because that version cannot
move underneath it. Correcting a published portfolio produces the next version,
copied from it; the published one keeps its numbers and its results.

**An edit is checked before it is stored, against the same schema the file was
read with.** A cell that would fail validation is refused with the reason, in
the words the person filling it in would use, rather than accepted and reported
as a finding later.

**The file stays the source of truth.** After every accepted edit the OED file
is rewritten from the rows and re-attached, so the file a run consumes is the
data the screen showed -- there is never a hidden overlay that the engine does
not see.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from cass_oed.schema import DataType, FieldSpec, FileKind, field_map, fields_for

from .models import ExposureState, ExposureVersion
from .services import KIND_BY_ROLE, ROLE_BY_KIND, ExposureError, attach_file, run_validation


class RowError(Exception):
    """Raised when an edit cannot be applied, with the fields that refused it."""

    def __init__(self, problems: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{name}: {why}" for name, why in problems.items()))
        self.problems = dict(problems)


def columns(kind: FileKind) -> list[dict[str, Any]]:
    """What the editor may show and what each cell will accept.

    The form is built from this, so the constraints a person types against are
    the constraints the file is validated against -- one description of a
    column, not a screen's guess at one.
    """
    return [
        {
            "name": spec.name,
            "label": spec.business_label,
            "help": spec.business_help,
            "required": spec.required,
            "type": str(spec.dtype),
            "allowed": list(spec.allowed) if spec.allowed else None,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "is_tiv": spec.is_tiv,
            "is_financial_term": spec.is_financial_term,
        }
        for spec in fields_for(kind)
    ]


def attached_kinds(version: ExposureVersion) -> list[FileKind]:
    """The files this version actually holds, in the order they are worked on."""
    from apps.artifacts.models import ArtifactLink

    roles = set(
        ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=version.id, direction="input"
        ).values_list("role", flat=True)
    )
    return [kind for kind in FileKind if ROLE_BY_KIND[kind] in roles]


def read_rows(version: ExposureVersion, kind: FileKind) -> list[dict[str, str]]:
    """Every row of one file, as text, in the order it was supplied.

    Text rather than coerced values: what the file says is what a person is
    correcting, and a number that has been through a float on the way to the
    screen is no longer the number they typed.
    """
    from apps.artifacts.models import ArtifactLink
    from apps.common.storage import get_store

    link = (
        ArtifactLink.objects.filter(
            subject_type="exposure_version",
            subject_id=version.id,
            direction="input",
            role=ROLE_BY_KIND[FileKind(kind)],
        )
        .select_related("artifact")
        .first()
    )
    if link is None:
        raise ExposureError(
            f"This version has no {FileKind(kind).label.lower()} attached."
        )

    store = get_store()
    with store.open(link.artifact.uri) as handle:
        payload = handle.read()
    text = payload.decode("utf-8-sig")
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def check(kind: FileKind, values: Mapping[str, Any]) -> dict[str, str]:
    """What is wrong with one row, field by field, in business language."""
    specs = field_map(FileKind(kind))
    problems: dict[str, str] = {}

    for name, raw in values.items():
        spec = specs.get(name)
        if spec is None:
            problems[name] = "CASS does not read this column."
            continue
        text = "" if raw is None else str(raw).strip()
        if not text:
            if spec.required:
                problems[name] = f"{spec.business_label} is required."
            continue
        why = _refuse(spec, text)
        if why:
            problems[name] = why

    for spec in fields_for(FileKind(kind)):
        if spec.required and not str(values.get(spec.name, "") or "").strip():
            problems.setdefault(spec.name, f"{spec.business_label} is required.")

    return problems


def _refuse(spec: FieldSpec, text: str) -> str:
    """Why this value cannot go in this field, or an empty string."""
    if spec.allowed is not None and text.upper() not in spec.allowed:
        return (
            f"{spec.business_label} accepts only {', '.join(spec.allowed)}."
        )

    match spec.dtype:
        case DataType.INTEGER:
            try:
                number = Decimal(text)
                if number != number.to_integral_value():
                    raise InvalidOperation
            except (InvalidOperation, ArithmeticError):
                return f"{spec.business_label} must be a whole number."
            return _range(spec, Decimal(text))
        case DataType.DECIMAL | DataType.MONEY | DataType.RATE | DataType.LATITUDE | DataType.LONGITUDE:
            try:
                number = Decimal(text)
            except (InvalidOperation, ArithmeticError):
                return f"{spec.business_label} must be a number."
            return _range(spec, number)
        case DataType.DATE:
            import datetime as dt

            try:
                dt.date.fromisoformat(text)
            except ValueError:
                return f"{spec.business_label} must be a date, as 2026-06-11."
        case DataType.FLAG:
            if text.lower() not in ("1", "0", "y", "n", "yes", "no", "true", "false"):
                return f"{spec.business_label} must be yes or no."
        case DataType.CURRENCY:
            if len(text) != 3 or not text.isalpha():
                return f"{spec.business_label} must be a three-letter code, as USD."
    return ""


def _range(spec: FieldSpec, number: Decimal) -> str:
    if spec.minimum is not None and number < Decimal(str(spec.minimum)):
        return f"{spec.business_label} cannot be below {spec.minimum:g}."
    if spec.maximum is not None and number > Decimal(str(spec.maximum)):
        return f"{spec.business_label} cannot be above {spec.maximum:g}."
    return ""


def write_rows(
    version: ExposureVersion,
    kind: FileKind,
    rows: Sequence[Mapping[str, Any]],
    *,
    actor=None,
    filename: str = "",
) -> None:
    """Rewrite one file from its rows and re-attach it.

    The columns are the ones the file already had, so a correction does not
    quietly add or drop columns the supplier did not send.
    """
    kind = FileKind(kind)
    if not rows:
        raise ExposureError(
            f"A {kind.label.lower()} with no rows would not be a correction, it "
            "would be a different portfolio. Remove the file instead."
        )
    names = list(rows[0])
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in names})

    attach_file(
        version,
        kind,
        buffer.getvalue().encode("utf-8"),
        filename=filename or f"{ROLE_BY_KIND[kind]}.csv",
        actor=actor,
    )


def edit_row(
    version: ExposureVersion,
    kind: FileKind,
    row_number: int,
    values: Mapping[str, Any],
    *,
    actor=None,
) -> dict[str, str]:
    """Apply one correction, and revalidate what it changed.

    ``row_number`` is 1-based over the data rows, as the screen numbers them.
    """
    _require_draft(version)
    kind = FileKind(kind)
    rows = read_rows(version, kind)
    if not 1 <= row_number <= len(rows):
        raise ExposureError(
            f"Row {row_number} is not in this file, which has {len(rows)} row(s)."
        )

    updated = dict(rows[row_number - 1])
    updated.update({name: "" if value is None else str(value) for name, value in values.items()})
    problems = check(kind, updated)
    if problems:
        raise RowError(problems)

    rows[row_number - 1] = updated
    write_rows(version, kind, rows, actor=actor)
    run_validation(version, actor=actor)
    return updated


def add_row(
    version: ExposureVersion, kind: FileKind, values: Mapping[str, Any], *, actor=None
) -> dict[str, str]:
    """Add a row to a file, checked the same way an edit is."""
    _require_draft(version)
    kind = FileKind(kind)
    rows = read_rows(version, kind)
    names = list(rows[0]) if rows else [spec.name for spec in fields_for(kind)]
    row = {name: "" for name in names}
    row.update({name: "" if value is None else str(value) for name, value in values.items()})
    problems = check(kind, row)
    if problems:
        raise RowError(problems)

    rows.append(row)
    write_rows(version, kind, rows, actor=actor)
    run_validation(version, actor=actor)
    return row


def delete_row(
    version: ExposureVersion, kind: FileKind, row_number: int, *, actor=None
) -> None:
    """Remove a row. The last one cannot go: an empty file is not a portfolio."""
    _require_draft(version)
    kind = FileKind(kind)
    rows = read_rows(version, kind)
    if not 1 <= row_number <= len(rows):
        raise ExposureError(
            f"Row {row_number} is not in this file, which has {len(rows)} row(s)."
        )
    if len(rows) == 1:
        raise ExposureError(
            "This is the last row in the file. Removing it would leave a "
            "portfolio with nothing in it."
        )
    del rows[row_number - 1]
    write_rows(version, kind, rows, actor=actor)
    run_validation(version, actor=actor)


def correct(version: ExposureVersion, *, actor=None) -> ExposureVersion:
    """The next version of a published portfolio, copied from it and editable.

    Not a fork: it supersedes, carries the same name, and starts from exactly
    the files that were published, so the difference between the two is only
    what somebody then corrects.
    """
    from apps.artifacts.models import ArtifactLink
    from apps.common.storage import get_store

    from .services import next_version_number

    if not version.is_frozen:
        raise ExposureError(
            f"{version} is still a draft, so it can be corrected where it is."
        )

    corrected = ExposureVersion.objects.create(
        project=version.project,
        name=version.name,
        version=next_version_number(version.project, version.name),
        state=ExposureState.DRAFT,
        source_description=version.source_description,
        valuation_date=version.valuation_date,
        run_currency=version.run_currency,
        source_lineage={
            **(version.source_lineage or {}),
            "corrected_from": str(version.id),
            "corrected_from_version": version.version,
        },
        supersedes=version,
        created_by=actor,
        updated_by=actor,
    )

    store = get_store()
    links = ArtifactLink.objects.filter(
        subject_type="exposure_version", subject_id=version.id, direction="input"
    ).select_related("artifact")
    for link in links:
        kind = KIND_BY_ROLE.get(link.role)
        if kind is None:
            continue
        with store.open(link.artifact.uri) as handle:
            payload = handle.read()
        attach_file(
            corrected,
            kind,
            payload,
            filename=link.artifact.original_filename or f"{link.role}.csv",
            actor=actor,
        )

    run_validation(corrected, actor=actor)
    return corrected


def _require_draft(version: ExposureVersion) -> None:
    if version.is_frozen:
        raise ExposureError(
            f"{version} is published, and a published portfolio is what its runs "
            "point at. Create a correction, which copies it into version "
            f"{version.version + 1} for you to edit."
        )


def rows_page(
    version: ExposureVersion,
    kind: FileKind,
    *,
    offset: int = 0,
    limit: int = 50,
    search: str = "",
) -> dict[str, Any]:
    """One page of rows, with the row numbers the editor edits by."""
    rows = read_rows(version, kind)
    numbered: Iterator[tuple[int, dict[str, str]]] = enumerate(rows, start=1)
    if search:
        needle = search.strip().lower()
        numbered = (
            (number, row)
            for number, row in numbered
            if any(needle in str(value).lower() for value in row.values())
        )
    selected = list(numbered)
    window = selected[offset : offset + limit]
    return {
        "kind": str(FileKind(kind)),
        "columns": columns(kind),
        "count": len(selected),
        "total": len(rows),
        "rows": [{"row_number": number, "values": row} for number, row in window],
        "editable": not version.is_frozen,
    }
