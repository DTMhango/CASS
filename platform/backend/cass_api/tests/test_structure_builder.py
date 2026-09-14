"""Building a financial structure on the platform.

What is held here is mostly what may not be written: a policy on an account
with no locations, a share typed as a percentage, a catastrophe excess with no
occurrence terms, a surplus share that does not name each risk and its share,
and a contract type the engine would leave out of the ceded loss. The rules per
type are oasislmf's own, so a structure built here is one the engine accepts.
The rest is that what is written is the portfolio's own OED, validated and
publishable like an imported one.
"""

from __future__ import annotations

import pytest

from apps.exposure import editing
from apps.exposure.models import ExposureVersion
from cass_oed.schema import FileKind

from .conftest import API
from .test_exposure_journey import create_version, upload

pytestmark = pytest.mark.django_db


@pytest.fixture()
def draft(api, project, earthquake_location_csv):
    """Three Indonesian locations on account ACC-1, and no structure at all."""
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)
    return exposure_id


def build(api, exposure_id, path, body):
    return api.post(f"{API}/exposure-versions/{exposure_id}/structure/{path}/", body, format="json")


def rows(exposure_id, kind):
    return editing.read_rows(ExposureVersion.objects.get(id=exposure_id), kind)


POLICY = {
    "account": "ACC-1",
    "policy": "POL-1",
    "perils": "QEQ",
    "deductible": "50000",
    "layers": [
        {"attachment": "0", "limit": "5000000", "participation": "0.5"},
        {"attachment": "5000000", "limit": "5000000", "participation": "0.25"},
    ],
}

CAT_XL = {
    "type": "CXL",
    "name": "Indonesia cat XL",
    "perils": "QEQ",
    "occurrence_attachment": "2000000",
    "occurrence_limit": "5000000",
    "whole_portfolio": True,
}


# -- policies ---------------------------------------------------------------------------

def test_a_policy_and_its_layers_are_written_into_a_portfolio_that_had_none(api, draft):
    response = build(api, draft, "policies", POLICY)

    assert response.status_code == 201, response.data
    assert [layer["layer_number"] for layer in response.data["layers"]] == [1, 2]
    written = rows(draft, FileKind.ACCOUNT)
    assert written[0]["AccCurrency"] == "IDR"
    assert written[0]["PortNumber"] == "1"
    # OED requires the peril and a basis wherever a policy term carries a value.
    assert (written[0]["PolPeril"], written[0]["PolDedType6All"]) == ("QEQ", "0")
    assert ExposureVersion.objects.get(id=draft).state == "validated"


def test_a_policy_on_an_account_with_no_locations_is_refused(api, draft):
    response = build(api, draft, "policies", {**POLICY, "account": "ACC-9"})

    assert response.status_code == 400
    assert "would cover nothing" in response.data["fields"]["AccNumber"]


def test_a_share_typed_as_a_percentage_is_refused_with_how_to_write_it(api, draft):
    layers = [{"attachment": "0", "limit": "5000000", "participation": "25"}]

    response = build(api, draft, "policies", {**POLICY, "layers": layers})

    assert response.status_code == 400
    assert "0.25" in response.data["fields"]["layers.1.participation"]


def test_a_policy_is_written_once_and_can_be_removed(api, draft):
    build(api, draft, "policies", POLICY)

    again = build(api, draft, "policies", POLICY)
    removed = build(api, draft, "policies/remove", {"account": "ACC-1", "policy": "POL-1"})

    assert again.status_code == 400
    assert removed.status_code == 200
    assert removed.data["has_accounts"] is False


# -- contracts --------------------------------------------------------------------------

def test_a_cat_xl_over_the_whole_portfolio_reaches_every_location(api, draft):
    response = build(api, draft, "contracts", CAT_XL)

    assert response.status_code == 201, response.data
    [contract] = response.data["contracts"]
    assert contract["applied_by_the_engine"] is True
    assert contract["locations_reached"] == 3
    [info] = rows(draft, FileKind.REINS_INFO)
    # Written explicitly: the engine applies a contract only at a risk level it names.
    assert (info["RiskLevel"], info["CededPercent"], info["PlacedPercent"]) == ("SEL", "1", "1")
    assert rows(draft, FileKind.REINS_SCOPE)[0]["PortNumber"] == "1"


def test_a_cat_xl_without_its_occurrence_terms_says_what_they_are(api, draft):
    response = build(api, draft, "contracts", {**CAT_XL, "occurrence_limit": ""})

    assert response.status_code == 400
    assert "the most it pays for one event" in response.data["fields"]["OccLimit"]


def test_a_quota_share_on_one_location_leaves_the_rest_retained(api, draft):
    body = {
        "type": "QS",
        "perils": "QEQ",
        "ceded_percent": "0.3",
        "scope": [{"account": "ACC-1", "location": "LOC-1"}],
    }

    response = build(api, draft, "contracts", body)

    assert response.status_code == 201, response.data
    assert response.data["contracts"][0]["locations_reached"] == 1
    assert response.data["uncovered_locations"] == 2


def test_a_surplus_share_names_each_risk_with_the_share_ceded_on_it(api, draft):
    body = {
        "type": "SS",
        "perils": "QEQ",
        "risk_level": "LOC",
        "risk_limit": "1000000",
        "scope": [
            {"account": "ACC-1", "location": "LOC-1", "ceded_percent": "0.4"},
            {"account": "ACC-1", "location": "LOC-3", "ceded_percent": "0.6"},
        ],
    }

    response = build(api, draft, "contracts", body)

    assert response.status_code == 201, response.data
    [info] = rows(draft, FileKind.REINS_INFO)
    assert (info["RiskLevel"], info["CededPercent"]) == ("LOC", "")
    assert [row["CededPercent"] for row in rows(draft, FileKind.REINS_SCOPE)] == ["0.4", "0.6"]


def test_a_surplus_share_without_a_share_per_risk_or_over_the_portfolio_is_refused(api, draft):
    over_everything = build(
        api, draft, "contracts",
        {"type": "SS", "perils": "QEQ", "risk_level": "LOC", "whole_portfolio": True},
    )
    without_shares = build(
        api, draft, "contracts",
        {"type": "SS", "perils": "QEQ", "risk_level": "LOC",
         "scope": [{"account": "ACC-1", "location": "LOC-1"}]},
    )
    wrong_level = build(
        api, draft, "contracts",
        {"type": "SS", "perils": "QEQ", "risk_level": "LOC",
         "scope": [{"account": "ACC-1", "ceded_percent": "0.5"}]},
    )

    assert "refuses one scoped to the whole portfolio" in over_everything.data["fields"]["scope"]
    assert "needs the share ceded" in without_shares.data["fields"]["scope.1.ceded_percent"]
    assert "the location" in wrong_level.data["fields"]["scope.1"]


def test_a_contract_type_the_engine_would_leave_out_is_refused(api, draft):
    response = build(
        api, draft, "contracts",
        {"type": "PR", "perils": "QEQ", "risk_level": "LOC", "whole_portfolio": True},
    )

    assert response.status_code == 400
    assert "not a contract type CASS applies" in response.data["fields"]["ReinsType"]


def test_contracts_are_numbered_in_order_and_removed_with_their_scope(api, draft):
    build(api, draft, "contracts", CAT_XL)
    second = build(
        api, draft, "contracts",
        {"type": "QS", "perils": "QEQ", "ceded_percent": "0.3", "whole_portfolio": True,
         "inuring_priority": 2},
    )

    assert [item["number"] for item in second.data["contracts"]] == [1, 2]
    first_removed = build(api, draft, "contracts/remove", {"number": 1})
    assert [item["number"] for item in first_removed.data["contracts"]] == [2]
    assert {row["ReinsNumber"] for row in rows(draft, FileKind.REINS_SCOPE)} == {"2"}

    both_removed = build(api, draft, "contracts/remove", {"number": 2})
    assert both_removed.data["has_contracts"] is False
    assert FileKind.REINS_INFO not in editing.attached_kinds(ExposureVersion.objects.get(id=draft))


# -- the version the structure lives in -------------------------------------------------

def test_a_built_structure_validates_and_publishes_like_an_imported_one(api, draft):
    build(api, draft, "policies", POLICY)
    build(api, draft, "contracts", CAT_XL)

    published = api.post(f"{API}/exposure-versions/{draft}/publish/")

    assert published.status_code == 200, published.data
    structure = api.get(f"{API}/exposure-versions/{draft}/financial-structure/").data
    assert structure["has_accounts"] and structure["has_contracts"]
    assert not [item for item in structure["findings"] if item["blocking"]]


def test_a_published_portfolio_is_not_rebuilt_in_place(api, draft):
    api.post(f"{API}/exposure-versions/{draft}/validate/")
    api.post(f"{API}/exposure-versions/{draft}/publish/")

    response = build(api, draft, "policies", POLICY)

    assert response.status_code == 409
    assert "Correct it in a new version" in response.data["detail"]


def test_somebody_outside_the_project_cannot_build_on_its_portfolio(client_for, outsider, draft):
    response = build(client_for(outsider), draft, "contracts", CAT_XL)

    assert response.status_code == 404
