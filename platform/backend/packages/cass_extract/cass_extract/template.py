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
_EXAMPLE_FILL = PatternFill("solid", fgColor="F4F6FB")

#: Written in the first cell of the worked example, so a person can see what a
#: filled-in row looks like and the reader can tell it from real data.
EXAMPLE_MARKER = "EXAMPLE ROW - delete before uploading"


def workbook(
    *,
    project_reference: str = "",
    risks: Sequence[Mapping[str, Any]] = (),
    policies: Sequence[Mapping[str, Any]] = (),
    contracts: Sequence[Mapping[str, Any]] = (),
    scope: Sequence[Mapping[str, Any]] = (),
    generated: dt.date | None = None,
) -> bytes:
    """A CASS intake workbook, blank or pre-filled.

    The row arguments let the same generator produce a populated file:
    an export of what CASS holds, or a migration from another shape. A template
    and an export that disagreed about column order would be two formats
    wearing one name.
    """
    book = Workbook()
    book.remove(book.active)

    _guide(book, project_reference=project_reference, generated=generated)
    for sheet, rows in (
        (Sheet.RISK, risks),
        (Sheet.POLICY, policies),
        (Sheet.CONTRACT, contracts),
        (Sheet.SCOPE, scope),
    ):
        _sheet(book, sheet, rows)

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

    # A worked row, in the sheet rather than only in the guidance. It is the
    # first thing a person looks at, and a template that describes its columns
    # without showing one filled in is the template that comes back wrong. It
    # says beside itself that it must go, and the reader drops it anyway, so
    # neither a careful nor a hurried user can turn it into exposure. The
    # reinsurance sheets show several, because a layered programme is two rows
    # sharing a contract number and one row cannot show that.
    rows = list(rows)
    first_data_row = 2
    if not rows:
        # Only on a blank template. An export of a portfolio CASS already holds
        # is data, and a worked example sitting on top of it would be a row
        # somebody has to notice is not theirs.
        examples = profile.SHEET_EXAMPLES.get(sheet) or (
            {item.name: item.example for item in columns},
        )
        for offset, example in enumerate(examples, start=2):
            for position, item in enumerate(columns, start=1):
                cell = worksheet.cell(row=offset, column=position, value=example.get(item.name, ""))
                cell.font = Font(italic=True, color="6B7280")
                cell.fill = _EXAMPLE_FILL
            # The marker goes beside the row rather than in its first cell, so
            # every column still shows its own example -- including the Policy
            # ID, which is the one most likely to come back in the wrong shape.
            note = worksheet.cell(row=offset, column=len(columns) + 1, value=EXAMPLE_MARKER)
            note.font = Font(italic=True, bold=True, color="6B7280")
            note.fill = _EXAMPLE_FILL
        worksheet.column_dimensions[get_column_letter(len(columns) + 1)].width = (
            len(EXAMPLE_MARKER) + 2
        )
        first_data_row = 2 + len(examples)

    for offset, row in enumerate(rows, start=first_data_row):
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
        "Policies sheet. The Policy ID appears on both and is what joins them, so "
        "it has to match exactly. The two reinsurance sheets are optional: fill "
        "them in only if the portfolio is reinsured."
    )
    write()
    write(
        "Every sheet opens with worked example rows: shaded, with a note beside "
        "each saying so. Type over them or delete them -- CASS ignores them either "
        "way."
    )
    write()
    write(
        "The Policy ID is the one from the premium system: underwriting year, "
        "inception month and business reference, joined by underscores, as in "
        "2026_06_PFAC8716. The business reference on its own does not say which "
        "year's placement a risk belongs to."
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
        "Policy terms are optional. Without a Policies sheet CASS reports "
        "ground-up loss only. With one, every policy is written as stated: a layer "
        "with no limit pays the whole loss above its attachment, so its insured "
        "loss is its ground-up loss, and CASS lists it as possibly overstated "
        "beside every insured and net result. Write 0 as the attachment of a layer "
        "that pays from the first loss, and the sum insured as the limit of a share "
        "of a whole risk."
    )
    write()
    write(
        "Most policies cover one risk. For those, put the deductible and limit on "
        "the Policies sheet only, and leave Risk deductible and Risk limit blank. "
        "CASS takes a risk deductible off first and then the policy deductible off "
        "what is left, so the same 25,000 written on both sheets is charged twice: "
        "a 120,000 loss pays 70,000 instead of 95,000. Use the risk columns only "
        "where a policy covers several properties that each carry their own terms."
    )
    write()
    write(
        "A policy with more than one layer has one row per layer on the Policies "
        "sheet, all with the same Policy ID and Policy reference. Repeat the policy "
        "deductible and limit on every layer's row: each layer reads them from its "
        "own row, and a layer whose row leaves them blank is calculated without them."
    )
    write()
    write(
        "Reinsurance takes two sheets. Reinsurance contracts holds each contract's "
        "terms, one row per layer: a two-layer catastrophe programme is two rows "
        "with the same contract number, layer 1 and layer 2, and the same inuring "
        "priority. Reinsurance scope says what each contract covers, once per "
        "contract: a row with only the contract number covers the whole portfolio, "
        "and a row naming a Policy ID covers that policy. The example rows show a "
        "30% quota share on one policy, then a two-layer catastrophe excess of "
        "loss over the whole portfolio."
    )
    write()
    write(
        "Reinsurance applies to insured loss, so CASS writes it only where it also "
        "writes the policy terms. Contracts are checked against the same rules as "
        "the Financial structure screen when the workbook is imported, and any "
        "problem is listed then."
    )
    write()

    for sheet in Sheet:
        write(str(sheet), bold=True)
        write(
            "Column", "Required", "Example", "What it is", "If left blank",
            "What CASS does then", bold=True,
        )
        for item in profile.columns_for(sheet):
            write(
                item.name,
                "Yes" if item.required else "No",
                item.example,
                item.help_text,
                _blank_label(item),
                item.blank_effect or item.purpose,
            )
        write()

    write("Filled in by CASS, so the template does not ask for them", bold=True)
    for name, reason in profile.DERIVED_FIELDS.items():
        write(name, "", "", reason)


def _blank_label(item: Column) -> str:
    return {
        profile.WhenBlank.REFUSED: "Not allowed",
        profile.WhenBlank.ASSUMED: "An assumption fills it",
        profile.WhenBlank.ALLOCATED: "Derived from the policy total",
        profile.WhenBlank.ABSENT: "Left absent",
        profile.WhenBlank.LIMITS_PERSPECTIVE: "A perspective becomes unavailable",
    }[item.when_blank]
