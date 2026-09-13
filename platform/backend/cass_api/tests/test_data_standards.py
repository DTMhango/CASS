"""The OED standards registry.

Section 17 pins the OED version in use, and section 8 makes moving to OED 5 a
decision taken against evidence rather than a dependency bump. These hold the
registry to what makes that possible.

The specification is stored as its owner published it, so a comparison made
next year is against the same bytes a decision was taken against this year.
Two versions can be compared field by field, with a changed requirement kept
separate from a changed type, because one invalidates a portfolio that
validated yesterday and the other is a reader change.

And the subset CASS actually reads is reported against the standard. That
subset is a legitimate position; leaving it unstated is what makes it dangerous.
"""

from __future__ import annotations

import json

import pytest

from apps.standards import registry, services
from apps.standards.models import DataStandardVersion, StandardState
from cass_oed.schema import OED_SCHEMA_VERSION

from .conftest import API

pytestmark = pytest.mark.django_db


def specification(**overrides) -> bytes:
    """A specification in the shape ODS Tools publishes one."""
    document = {
        "input_fields": {
            "Loc": {
                "portnumber": {
                    "Input Field Name": "PortNumber",
                    "Type & Description": "Portfolio number",
                    "Property field status": "R",
                    "Data Type": "varchar(20)",
                    "Default": "n/a",
                    "Valid value range": "n/a",
                },
                "locnumber": {
                    "Input Field Name": "LocNumber",
                    "Type & Description": "Location number",
                    "Property field status": "R",
                    "Data Type": "varchar(20)",
                    "Default": "n/a",
                    "Valid value range": "n/a",
                },
                "yearbuilt": {
                    "Input Field Name": "YearBuilt",
                    "Type & Description": "Year built",
                    "Property field status": "O",
                    "Data Type": "tinyint",
                    "Default": "0",
                    "Valid value range": "n/a",
                },
            },
            "Acc": {
                "accnumber": {
                    "Input Field Name": "AccNumber",
                    "Type & Description": "Account number",
                    "Property field status": "R",
                    "Data Type": "varchar(20)",
                    "Default": "n/a",
                    "Valid value range": "n/a",
                }
            },
            "null": {"ignored": {"Input Field Name": "Nothing"}},
        }
    }
    document["input_fields"].update(overrides)
    return json.dumps(document).encode("utf-8")


@pytest.fixture()
def registered(db, modeller) -> DataStandardVersion:
    return services.register(
        specification(),
        version=OED_SCHEMA_VERSION,
        source="ODS Tools 5.0.8",
        actor=modeller,
    )


# -- reading the specification ----------------------------------------------

def test_a_specification_is_read_into_fields_by_file():
    parsed = registry.read_specification(specification())

    assert set(parsed) == {"Loc", "Acc"}
    assert parsed["Loc"]["PortNumber"].required is True
    assert parsed["Loc"]["YearBuilt"].required is False
    assert parsed["Loc"]["YearBuilt"].data_type == "tinyint"


def test_something_that_is_not_a_specification_is_refused():
    with pytest.raises(registry.StandardError, match="readable JSON"):
        registry.read_specification(b"not json at all")

    with pytest.raises(registry.StandardError, match="input_fields"):
        registry.read_specification(b'{"something": "else"}')


# -- the registry record -----------------------------------------------------

def test_registering_keeps_the_specification_as_its_owner_published_it(registered):
    """A comparison made next year is against the bytes the decision used."""
    assert registered.checksum.startswith("sha256:")
    assert registered.field_count == 4
    assert registered.file_kinds == {"Acc": 1, "Loc": 3}

    read_back = services.specification_of(registered)
    assert read_back["Loc"]["PortNumber"].description == "Portfolio number"


def test_registering_the_same_version_twice_leaves_one_record(registered, modeller):
    services.register(
        specification(),
        version=OED_SCHEMA_VERSION,
        source="ODS Tools 5.0.8",
        actor=modeller,
    )

    assert DataStandardVersion.objects.filter(version=OED_SCHEMA_VERSION).count() == 1


def test_a_registered_version_is_a_candidate_until_somebody_adopts_it(registered):
    assert registered.state == StandardState.CANDIDATE
    assert registered.is_active is False


def test_adopting_one_version_supersedes_the_one_it_replaces(registered, modeller):
    later = services.register(
        specification(), version="5.0.0", source="ODS Tools 5.0.8", actor=modeller
    )
    services.adopt(registered, actor=modeller)
    services.adopt(later, actor=modeller)

    registered.refresh_from_db()
    later.refresh_from_db()
    assert later.state == StandardState.ACTIVE
    assert registered.state == StandardState.SUPERSEDED


# -- comparing two versions --------------------------------------------------

def test_a_comparison_separates_a_new_field_from_a_changed_requirement():
    earlier = registry.read_specification(specification())
    later = registry.read_specification(
        specification(
            Loc={
                "portnumber": {
                    "Input Field Name": "PortNumber",
                    "Property field status": "R",
                    "Data Type": "varchar(20)",
                },
                "locnumber": {
                    "Input Field Name": "LocNumber",
                    # Was required, now conditional: a portfolio that validated
                    # yesterday may not validate today.
                    "Property field status": "CR",
                    "Data Type": "varchar(20)",
                },
                "locperilscovered": {
                    "Input Field Name": "LocPerilsCovered",
                    "Property field status": "R",
                    "Data Type": "varchar(250)",
                },
            }
        )
    )

    changes = registry.compare(earlier, later)["files"]["Loc"]

    assert [item["name"] for item in changes["added"]] == ["LocPerilsCovered"]
    assert [item["name"] for item in changes["removed"]] == ["YearBuilt"]
    assert changes["requirement_changes"] == [
        {"field": "LocNumber", "before": "R", "after": "CR"}
    ]


def test_a_changed_type_is_reported_apart_from_a_changed_requirement():
    earlier = registry.read_specification(specification())
    later = registry.read_specification(
        specification(
            Loc={
                "portnumber": {
                    "Input Field Name": "PortNumber",
                    "Property field status": "R",
                    "Data Type": "varchar(50)",
                },
            }
        )
    )

    changes = registry.compare(earlier, later)["files"]["Loc"]

    assert changes["type_changes"] == [
        {"field": "PortNumber", "before": "varchar(20)", "after": "varchar(50)"}
    ]
    assert changes["requirement_changes"] == []


def test_two_identical_versions_compare_as_unchanged():
    parsed = registry.read_specification(specification())

    assert registry.compare(parsed, parsed)["unchanged"] is True


# -- what CASS reads of it ---------------------------------------------------

def test_the_coverage_report_names_required_fields_cass_does_not_read():
    parsed = registry.read_specification(
        specification(
            Loc={
                "somethingnew": {
                    "Input Field Name": "SomethingCassDoesNotRead",
                    "Property field status": "R",
                    "Data Type": "varchar(20)",
                }
            }
        )
    )

    report = registry.coverage(parsed)["location"]

    assert "SomethingCassDoesNotRead" in report["required_fields_not_read"]
    assert report["fields_read"] > 0


def test_the_coverage_report_names_columns_the_standard_does_not_define():
    """A column CASS reads that OED does not define will not travel."""
    parsed = registry.read_specification(specification())

    report = registry.coverage(parsed)["location"]

    # The fixture defines three location fields; everything else CASS reads is
    # outside it, which is exactly what the report is for.
    assert "BuildingTIV" in report["fields_not_in_the_standard"]


# -- the API -----------------------------------------------------------------

def test_the_registry_lists_what_is_pinned(registered, client_for, modeller):
    listing = client_for(modeller).get(f"{API}/data-standards/")

    assert listing.status_code == 200
    row = listing.data["results"][0]
    assert row["version"] == OED_SCHEMA_VERSION
    assert row["matches_the_reader"] is True
    assert row["source"] == "ODS Tools 5.0.8"


def test_two_versions_can_be_compared_through_the_api(registered, client_for, modeller):
    services.register(
        specification(), version="5.0.0", source="ODS Tools 5.0.8", actor=modeller
    )

    comparison = client_for(modeller).get(
        f"{API}/data-standards/diff/?from={OED_SCHEMA_VERSION}&to=5.0.0"
    )

    assert comparison.status_code == 200
    assert comparison.data["unchanged"] is True


def test_comparing_against_a_version_nobody_registered_says_to_register_it(
    registered, client_for, modeller
):
    missing = client_for(modeller).get(
        f"{API}/data-standards/diff/?from={OED_SCHEMA_VERSION}&to=9.9.9"
    )

    assert missing.status_code == 404
    assert "Register the specification first" in missing.data["detail"]


def test_adopting_a_version_the_reader_does_not_implement_is_refused(
    registered, client_for, modeller
):
    """A registry disagreeing with the validator is worse than no registry."""
    later = services.register(
        specification(), version="5.0.0", source="ODS Tools 5.0.8", actor=modeller
    )

    refused = client_for(modeller).post(f"{API}/data-standards/{later.id}/adopt/")

    assert refused.status_code == 409
    assert "would leave the registry" in refused.data["detail"]
    later.refresh_from_db()
    assert later.state == StandardState.CANDIDATE


def test_adopting_the_version_the_reader_implements_records_the_decision(
    registered, client_for, modeller
):
    adopted = client_for(modeller).post(f"{API}/data-standards/{registered.id}/adopt/")

    assert adopted.status_code == 200
    assert adopted.data["state"] == StandardState.ACTIVE
    assert adopted.data["adopted_at"]


def test_the_registry_is_not_writable_by_an_analyst(registered, api):
    """Pinning a standard is a modeller's act, and adopting one is a decision."""
    refused = api.post(f"{API}/data-standards/{registered.id}/adopt/")

    assert refused.status_code == 403


# -- against the specifications ODS Tools actually ships ---------------------

@pytest.mark.integration
def test_the_real_oed_4_and_5_specifications_register_and_compare(ods_data_path, modeller):
    """The comparison section 8 asks for, against the published releases.

    Marked integration because it needs the pinned ODS Tools data directory.
    What it proves is that the registry reads the real files rather than the
    shape of a fixture, and that the OED 5 decision has a field-level
    comparison to rest on rather than a version number.
    """
    four = services.register_from_path(
        ods_data_path / "OpenExposureData_4.0.0Spec.json",
        version="4.0.0",
        source="ODS Tools 5.0.8",
        actor=modeller,
    )
    five = services.register_from_path(
        ods_data_path / "OpenExposureData_5.0.0Spec.json",
        version="5.0.0",
        source="ODS Tools 5.0.8",
        actor=modeller,
    )

    assert four.field_count > 100
    assert set(four.file_kinds) >= {"Loc", "Acc", "ReinsInfo", "ReinsScope"}

    changes = registry.compare(
        services.specification_of(four), services.specification_of(five)
    )
    assert changes["unchanged"] is False

    # And the subset CASS reads is reported against the real standard, which is
    # what makes the subset a position rather than an accident.
    coverage = registry.coverage(services.specification_of(four))
    assert coverage["location"]["fields_defined"] > 100
    assert coverage["location"]["fields_not_in_the_standard"] == []


# -- the command that registers one ------------------------------------------

def test_the_registration_command_runs_and_adopts(tmp_path, modeller):
    """A command that cannot be invoked registers nothing.

    Its option is ``--release`` rather than ``--version`` because Django's own
    base command defines the latter, and the clash is an error at parse time --
    which is exactly the kind of thing that stays hidden when the service
    underneath is tested and the command is not.
    """
    from django.core.management import call_command

    (tmp_path / f"OpenExposureData_{OED_SCHEMA_VERSION}Spec.json").write_bytes(
        specification()
    )

    call_command(
        "register_data_standard",
        "--root",
        str(tmp_path),
        "--release",
        OED_SCHEMA_VERSION,
        "--source",
        "ODS Tools 5.0.8",
        "--adopt",
        OED_SCHEMA_VERSION,
        "--actor",
        modeller.username,
    )

    record = DataStandardVersion.objects.get(version=OED_SCHEMA_VERSION)
    assert record.state == StandardState.ACTIVE
    assert record.adopted_by == modeller


def test_the_command_refuses_a_release_the_directory_does_not_hold(tmp_path):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="is not a file"):
        call_command(
            "register_data_standard", "--root", str(tmp_path), "--release", "9.9.9"
        )
