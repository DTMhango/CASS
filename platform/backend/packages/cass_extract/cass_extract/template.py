"""Generating the blank CASS intake template.

The workbook a user downloads, built from ``profile`` rather than maintained
alongside it. Nobody has to remember to update a spreadsheet when a column is
rebound, and the file a person fills in cannot describe a mapping the platform
does not implement.

The guidance sheet is not decoration. Every optional column says what happens
when it is left empty, in the same words the profile uses, because the single
most damaging thing a person can do with this file is type a plausible number
into a cell they do not actually know. A blank that reaches a named assumption
is auditable; a guess is not, and nothing downstream can tell the two apart.
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import profile
from .profile import GUIDE_SHEET, PROFILE_VERSION, Column, Sheet

#: Written into the guidance sheet so a completed file can be matched back to
#: the profile that produced it.
VERSION_CELL_LABEL = "CASS intake profile"

_REQUIRED_FILL = PatternFill("solid", fgColor="FFF3D6")
_HEADER_FILL = PatternFill("solid", fgColor="EDEDED")


def workbook(
    *,
    project_reference: str = "",
    risks: Sequence[Mapping[str, Any]] = (),
    policies: Sequence[Mapping[str, Any]] = (),
    generated: dt.date | None = None,
) -> bytes:
    """A CASS intake workbook, blank or pre-filled.

    ``risks`` and ``policies`` let the same generator produce a populated file:
    an export of what CASS holds, or a migration from another shape. A template
    and an export that disagreed about column order would be two formats
    wearing one name.
    """
    book = Workbook()
    book.remove(book.active)

    _guide(book, project_reference=project_reference, generated=generated)
    _sheet(book, Sheet.RISK, risks)
    _sheet(book, Sheet.POLICY, policies)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _sheet(book: Workbook, sheet: Sheet, rows: Iterable[Mapping[str, Any]]) -> None:
    columns = profile.columns_for(sheet)
    worksheet = book.create_sheet(str(sheet))

    for position, item in enumerate(columns, start=1):
        cell = worksheet.cell(row=1, column=position, value=item.name)
        cell.font = Font(bold=True)
        cell.fill = _REQUIRED_FILL if item.required else _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        # The help text as a cell comment would be lost by most exporters, so
        # it lives on the guidance sheet where it survives a round trip.
        worksheet.column_dimensions[get_column_letter(position)].width = max(
            12, min(28, len(item.name) + 6)
        )

    for offset, row in enumerate(rows, start=2):
        for position, item in enumerate(columns, start=1):
            worksheet.cell(row=offset, column=position, value=row.get(item.name, ""))

    worksheet.freeze_panes = "A2"


def _guide(book: Workbook, *, project_reference: str, generated: dt.date | None) -> None:
    worksheet = book.create_sheet(GUIDE_SHEET)
    widths = (34, 12, 12, 62, 22, 62)
    for position, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(position)].width = width

    row = 1

    def write(*values: Any, bold: bool = False) -> None:
        nonlocal row
        for position, value in enumerate(values, start=1):
            cell = worksheet.cell(row=row, column=position, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if bold:
                cell.font = Font(bold=True)
        row += 1

    write("CASS portfolio intake", bold=True)
    write(VERSION_CELL_LABEL, PROFILE_VERSION)
    write("OED schema", profile.as_dict()["oed_schema_version"])
    write("Project", project_reference or "(not set)")
    write("Generated", (generated or dt.date.today()).isoformat())
    write()
    write(
        "One row per risk on the Risks sheet, one row per policy or layer on the "
        "Policies sheet. The account reference appears on both and is what joins "
        "them, so it has to match exactly."
    )
    write()
    write(
        "Leave a cell blank where you do not know the answer. Every optional "
        "column below says what CASS does with an empty cell, and it is never "
        "'treat it as zero'. A blank reaches a named assumption that the run "
        "records; a guess typed into a cell cannot be told apart from evidence."
    )
    write()
    write(
        "Policy terms are optional. Without them CASS reports ground-up loss and "
        "says which perspectives the data does not support, rather than inventing "
        "a deductible."
    )
    write()

    for sheet in Sheet:
        write(str(sheet), bold=True)
        write(
            "Column", "Required", "OED field", "What it is", "If left blank",
            "What CASS does then", bold=True,
        )
        for item in profile.columns_for(sheet):
            write(
                item.name,
                "Yes" if item.required else "No",
                item.oed_field or "(CASS)",
                item.help_text,
                _blank_label(item),
                item.blank_effect or item.purpose,
            )
        write()

    write("Filled in by CASS, so the template does not ask for them", bold=True)
    for name, reason in profile.DERIVED_FIELDS.items():
        write(name, "", name, reason)
    write()

    write("OED fields this template does not cover", bold=True)
    for kind, names in profile.as_dict()["oed_fields_not_requested"].items():
        write(kind, "", ", ".join(names) or "(none)")


def _blank_label(item: Column) -> str:
    return {
        profile.WhenBlank.REFUSED: "Not allowed",
        profile.WhenBlank.ASSUMED: "An assumption fills it",
        profile.WhenBlank.ALLOCATED: "Derived from the policy total",
        profile.WhenBlank.ABSENT: "Left absent",
        profile.WhenBlank.LIMITS_PERSPECTIVE: "A perspective becomes unavailable",
    }[item.when_blank]
