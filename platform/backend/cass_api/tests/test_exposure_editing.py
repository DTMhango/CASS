"""Reading a portfolio on the platform, and correcting it there.

What this holds: a wrong cell is fixed where it is seen rather than in a
spreadsheet somewhere else; a value that would not validate is refused with the
reason before it is stored; the file a run consumes is rewritten from what the
screen showed; and a published version still cannot move, because a run points
at it.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.exposure.models import ExposureState, ExposureVersion

from .conftest import API

pytestmark = pytest.mark.django_db


def upload(api, exposure_id, kind, payload, filename="source.csv"):
    return api.post(
        f"{API}/exposure-versions/{exposure_id}/files/",
        {"kind": kind, "file": SimpleUploadedFile(filename, payload, "text/csv")},
        format="multipart",
    )


@pytest.fixture()
def portfolio(api, project, earthquake_location_csv) -> str:
    created = api.post(
        f"{API}/exposure-versions/",
        {"project": str(project.id), "name": "Pilot portfolio"},
        format="json",
    )
    exposure_id = created.data["id"]
    assert upload(api, exposure_id, "location", earthquake_location_csv).status_code == 201
    api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    return exposure_id


def rows(api, exposure_id, **params):
    return api.get(f"{API}/exposure-versions/{exposure_id}/rows/", params)


def edit(api, exposure_id, row_number, values, kind="location"):
    return api.post(
        f"{API}/exposure-versions/{exposure_id}/rows/edit/",
        {"kind": kind, "row_number": row_number, "values": values},
        format="json",
    )


# -- reading ----------------------------------------------------------------

def test_the_rows_of_a_portfolio_are_readable_on_the_platform(api, portfolio):
    response = rows(api, portfolio)

    assert response.status_code == 200
    assert response.data["total"] == 3
    first = response.data["rows"][0]
    assert first["row_number"] == 1
    assert first["values"]["LocNumber"] == "LOC-1"


def test_each_column_says_what_it_will_accept(api, portfolio):
    """The editor builds its fields from this, so a form cannot drift from the schema."""
    response = rows(api, portfolio)

    by_name = {item["name"]: item for item in response.data["columns"]}
    assert by_name["Latitude"]["type"] == "latitude"
    assert by_name["Latitude"]["minimum"] == -90.0
    assert by_name["LocNumber"]["required"] is True
    assert by_name["LocDedType6All"]["allowed"] == ["0"]
    assert by_name["BuildingTIV"]["label"] == "Building value"


def test_the_files_a_version_holds_are_named(api, portfolio):
    assert rows(api, portfolio).data["attached"] == ["location"]


def test_a_page_of_rows_can_be_asked_for_by_itself(api, portfolio):
    response = rows(api, portfolio, offset=1, limit=1)

    assert [item["row_number"] for item in response.data["rows"]] == [2]
    assert response.data["total"] == 3


def test_rows_can_be_searched_for_the_one_being_corrected(api, portfolio):
    response = rows(api, portfolio, search="LOC-3")

    assert response.data["count"] == 1
    assert response.data["rows"][0]["values"]["LocNumber"] == "LOC-3"


# -- correcting -------------------------------------------------------------

def test_a_cell_is_corrected_where_it_is_seen(api, portfolio):
    response = edit(api, portfolio, 2, {"OccupancyCode": "1050"})

    assert response.status_code == 200, response.data
    assert response.data["row"]["values"]["OccupancyCode"] == "1050"
    assert rows(api, portfolio).data["rows"][1]["values"]["OccupancyCode"] == "1050"


def test_the_correction_reaches_the_file_a_run_would_read(api, portfolio):
    """Not an overlay: the OED file itself is rewritten, so nothing can diverge."""
    from apps.exposure import services

    edit(api, portfolio, 1, {"BuildingTIV": "7250000"})

    version = ExposureVersion.objects.get(id=portfolio)
    files = services.load_files(version)
    assert files.location.rows[0].text("BuildingTIV") == "7250000"


def test_a_value_the_schema_refuses_is_not_stored(api, portfolio):
    response = edit(api, portfolio, 1, {"Latitude": "-999"})

    assert response.status_code == 400
    assert "Latitude" in response.data["fields"]
    assert "below" in response.data["fields"]["Latitude"]
    assert rows(api, portfolio).data["rows"][0]["values"]["Latitude"] == "-6.2088"


def test_a_required_field_cannot_be_emptied(api, portfolio):
    response = edit(api, portfolio, 1, {"LocNumber": ""})

    assert response.status_code == 400
    assert "required" in response.data["fields"]["LocNumber"]


def test_a_term_basis_outside_what_cass_calculates_is_refused(api, portfolio):
    response = edit(api, portfolio, 1, {"LocDed6All": "1000", "LocDedType6All": "1"})

    assert response.status_code == 400
    assert "LocDedType6All" in response.data["fields"]


def test_the_summaries_move_with_the_correction(api, portfolio):
    """A total insured value that still showed the old number would be a lie."""
    before = ExposureVersion.objects.get(id=portfolio).total_tiv

    response = edit(api, portfolio, 1, {"BuildingTIV": "9500000"})

    assert response.data["version"]["total_tiv"] != str(before)
    assert ExposureVersion.objects.get(id=portfolio).total_tiv > before


def test_a_row_can_be_added_and_removed(api, portfolio, earthquake_location_csv):
    added = api.post(
        f"{API}/exposure-versions/{portfolio}/rows/add/",
        {
            "kind": "location",
            "values": {
                "PortNumber": "1", "AccNumber": "ACC-1", "LocNumber": "LOC-4",
                "BuildingID": "1", "CountryCode": "ID", "Latitude": "-6.3",
                "Longitude": "106.9", "OccupancyCode": "1050",
                "ConstructionCode": "5000", "LocPerilsCovered": "QEQ",
                "BuildingTIV": "1000000", "ContentsTIV": "0", "LocCurrency": "IDR",
            },
        },
        format="json",
    )
    assert added.status_code == 201, added.data
    assert rows(api, portfolio).data["total"] == 4

    removed = api.post(
        f"{API}/exposure-versions/{portfolio}/rows/remove/",
        {"kind": "location", "row_number": 4},
        format="json",
    )
    assert removed.status_code == 200
    assert rows(api, portfolio).data["total"] == 3


# -- what a published version does instead ----------------------------------

def test_a_published_portfolio_is_not_edited_in_place(api, portfolio):
    api.post(f"{API}/exposure-versions/{portfolio}/publish/")

    response = edit(api, portfolio, 1, {"BuildingTIV": "1"})

    assert response.status_code == 409
    assert "correction" in response.data["detail"]
    assert rows(api, portfolio).data["editable"] is False


def test_correcting_a_published_portfolio_copies_it_into_the_next_version(api, portfolio):
    api.post(f"{API}/exposure-versions/{portfolio}/publish/")

    response = api.post(f"{API}/exposure-versions/{portfolio}/correct/")

    assert response.status_code == 201, response.data
    assert response.data["version"] == 2
    # Validated on arrival, because it was published a moment ago and nothing
    # has changed yet -- but not frozen, which is the point of making it.
    assert response.data["state"] == ExposureState.VALIDATED
    assert response.data["is_frozen"] is False
    corrected = response.data["id"]
    assert rows(api, corrected).data["total"] == 3
    assert rows(api, corrected).data["editable"] is True

    # And the published one still says what it said.
    assert ExposureVersion.objects.get(id=portfolio).state == ExposureState.PUBLISHED


def test_a_correction_of_a_draft_is_refused_because_it_can_be_edited(api, portfolio):
    response = api.post(f"{API}/exposure-versions/{portfolio}/correct/")

    assert response.status_code == 409
    assert "still a draft" in response.data["detail"]
