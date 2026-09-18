"""Promoting a template that carries policy terms and reinsurance.

The template route used to write a location file and nothing else, so a
portfolio brought in this way could only ever run at ground-up loss, however
carefully its Policies sheet was filled in. These tests hold the other half:
the terms a workbook states reach the OED files, and a term it does not state
is never supplied on its behalf.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

from apps.artifacts.models import ArtifactLink
from apps.common.storage import get_store
from apps.exposure import services
from apps.exposure.extract import import_portfolio
from apps.exposure.promotion import PolicyTerms, PromotionError, promote
from cass_oed.perspectives import Perspective, available_perspectives

from .conftest import API

_FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "cass_extract" / "tests"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from fixtures import (  # noqa: E402
    as_template,
    contract_row,
    financial_template,
    layer_row,
    policy_row,
    risk_row,
)

pytestmark = pytest.mark.django_db


def rows(version, role: str) -> list[dict[str, str]]:
    """Read back one OED file the promotion wrote, or nothing if it wrote none."""
    link = ArtifactLink.objects.filter(
        subject_type="exposure_version", subject_id=version.id, role=role
    ).first()
    if link is None:
        return []
    with get_store().open(link.artifact.uri) as handle:
        return list(csv.DictReader(io.StringIO(handle.read().decode("utf-8"))))


def imported(project, analyst, *, risks=None, policies=None, contracts=None, scope=None):
    base = financial_template()
    payload = as_template(
        base[0] if risks is None else risks,
        base[1] if policies is None else policies,
        base[2] if contracts is None else contracts,
        base[3] if scope is None else scope,
    )
    return import_portfolio(project, payload, filename="financial.xlsx", actor=analyst)


def perspectives(version) -> set[str]:
    return {
        str(item.perspective)
        for item in available_perspectives(services.load_files(version))
        if item.available
    }


# -- complete terms reach every file ------------------------------------------------------

@pytest.fixture()
def version(project, analyst):
    return promote(imported(project, analyst), name="Financial", actor=analyst)


def test_complete_terms_make_every_perspective_available(version):
    assert perspectives(version) == {
        str(Perspective.GROUND_UP),
        str(Perspective.INSURED),
        str(Perspective.REINSURANCE),
    }


def test_every_policy_layer_is_written_as_stated(version):
    account = rows(version, "oed_account")
    layers = {(row["AccNumber"], row["LayerNumber"]): row for row in account}

    assert set(layers) == {("F-ONE", "1"), ("F-TWO", "1"), ("F-TWO", "2"), ("F-MULTI", "1")}
    assert layers[("F-ONE", "1")]["LayerParticipation"] == "0.2"
    assert layers[("F-ONE", "1")]["PolDed6All"] == "25000.00"
    assert layers[("F-ONE", "1")]["PolDedType6All"] == "0"
    assert layers[("F-TWO", "2")]["LayerAttachment"] == "500000.00"
    assert layers[("F-TWO", "2")]["LayerLimit"] == "1500000.00"
    # No deductible on the workbook's row, so none is written.
    assert layers[("F-MULTI", "1")]["PolDed6All"] == ""
    assert layers[("F-MULTI", "1")]["PolPeril"] == ""


def test_a_risk_deductible_is_written_beside_the_risk(version):
    location = {row["AccNumber"]: row for row in rows(version, "oed_location")}
    assert location["F-TWO"]["LocDed6All"] == "10000.00"
    assert location["F-TWO"]["LocDedType6All"] == "0"
    assert location["F-TWO"]["LocPeril"] == "QEQ"
    assert location["F-ONE"]["LocDed6All"] == ""


def test_contracts_keep_the_workbooks_numbers_and_layers(version):
    info = rows(version, "oed_reins_info")
    assert [(row["ReinsNumber"], row["ReinsLayerNumber"], row["ReinsType"]) for row in info] == [
        ("1", "1", "QS"),
        ("2", "1", "CXL"),
        ("2", "2", "CXL"),
    ]
    assert info[2]["PlacedPercent"] == "0.85"
    assert {row["InuringPriority"] for row in info[1:]} == {"2"}


def test_reinstatement_terms_are_written_where_the_workbook_states_them(version):
    info = rows(version, "oed_reins_info")
    assert (info[1]["Reinstatement"], info[1]["ReinstatementCharge"], info[1]["ReinsPremium"]) == (
        "2", "1.25;1", "150000.00",
    )
    # Layer 2 states none: written blank, which a limited-cover run applies as the
    # engine does and says so.
    assert (info[2]["Reinstatement"], info[2]["ReinsPremium"]) == ("", "")


def test_reinstatements_on_a_quota_share_are_refused_at_import(project, analyst):
    contracts = [contract_row(1, 1, "QS", 1, **{"Ceded share": "0.3", "Reinstatements": "1"})]
    batch = imported(project, analyst, contracts=contracts, scope=[{"Contract number": "1"}])
    refused = [item["message"] for item in batch.findings if item["code"] == "contract_refused"]
    assert any("catastrophe excess of loss only" in message for message in refused)


def test_a_reinstatement_rate_needs_the_premium_it_is_charged_on(project, analyst):
    contracts = [contract_row(2, 1, "CXL", 1, **{
        "Attachment per event": "100000.00", "Limit per event": "400000.00",
        "Reinstatements": "1", "Reinstatement rate": "1",
    })]
    batch = imported(project, analyst, contracts=contracts, scope=[{"Contract number": "2"}])
    refused = [item["message"] for item in batch.findings if item["code"] == "contract_refused"]
    assert any("premium it is charged on" in message for message in refused)


def test_scope_is_written_once_per_contract(version):
    scope = rows(version, "oed_reins_scope")
    assert [(row["ReinsNumber"], row["AccNumber"]) for row in scope] == [
        ("1", "F-ONE"),
        ("2", ""),
    ]
    assert scope[1]["PortNumber"] == version.project.reference


def test_the_lineage_says_what_was_written(version):
    record = version.source_lineage["financial_structure"]
    assert record["applied"] is True
    assert record["policy_ids_written"] == 3
    assert record["possibly_overstated"]["policy_ids"] == []
    assert record["policy_rows_written"] == 4
    assert record["contracts_written"] == [1, 2]
    assert record["contract_layers_written"] == 3
    # A signed share below one says the values are at 100% of the risk.
    assert "100% of each risk" in version.source_lineage["value_basis"]


def test_a_layered_policy_value_is_counted_once(project, analyst):
    """The policy total on every layer's row is one value, not one per layer."""
    base = financial_template()
    policies = [
        *base[1][:3],
        layer_row("F-MULTI", "P-3", 1, attachment="0", limit="1000000.00", total="3000000.00"),
        layer_row("F-MULTI", "P-3", 2, attachment="1000000.00", limit="2000000.00",
                  total="3000000.00"),
    ]
    version = promote(
        imported(project, analyst, policies=policies), name="Layered", actor=analyst
    )
    multi = [row for row in rows(version, "oed_location") if row["AccNumber"] == "F-MULTI"]
    total = sum(
        sum(float(row[column] or 0) for column in ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV"))
        for row in multi
    )
    assert total == pytest.approx(3000000.00)


def test_layers_stating_different_policy_values_are_refused(project, analyst):
    base = financial_template()
    policies = [
        *base[1][:3],
        layer_row("F-MULTI", "P-3", 1, attachment="0", limit="1000000.00", total="3000000.00"),
        layer_row("F-MULTI", "P-3", 2, attachment="1000000.00", limit="2000000.00",
                  total="2500000.00"),
    ]
    with pytest.raises(PromotionError, match="different total insured values"):
        promote(imported(project, analyst, policies=policies), name="Bad", actor=analyst)


# -- incomplete terms pass through, and say so ----------------------------------------------------

def self_valued_risks():
    """The template's risks, with F-MULTI's sites valued on the Risks sheet."""
    risks = financial_template()[0]
    for risk in risks:
        if risk["Policy ID"] == "F-MULTI":
            risk["Total insured value"] = "1500000.00"
    return risks


def incomplete(project, analyst, *, row=True, **terms):
    """F-MULTI's policy row states a value and whatever ``terms`` add, or is missing."""
    base = financial_template()
    policies = [*base[1][:3]]
    if row:
        policies.append(policy_row("F-MULTI", "P-3", "3000000.00", **terms))
        return imported(project, analyst, policies=policies)
    return imported(project, analyst, risks=self_valued_risks(), policies=policies)


def test_a_policy_without_a_limit_keeps_its_ground_up_loss_and_is_flagged(project, analyst):
    """Nothing is dropped: the account is written with the blanks OED reads as no limit."""
    version = promote(incomplete(project, analyst), name="Pass-through", actor=analyst)

    multi = next(row for row in rows(version, "oed_account") if row["AccNumber"] == "F-MULTI")
    assert multi["LayerLimit"] == ""
    assert multi["LayerAttachment"] == ""
    assert multi["LayerParticipation"] == "1"
    assert str(Perspective.REINSURANCE) in perspectives(version)

    flagged = version.source_lineage["financial_structure"]["possibly_overstated"]
    assert flagged["policy_ids"] == ["F-MULTI"]
    assert flagged["uncapped"] == ["F-MULTI"]
    assert flagged["attachment_read_as_zero"] == ["F-MULTI"]
    assert flagged["tiv"] == "3000000.00"
    assert "may be overstated" in version.source_lineage["financial_structure"]["reason"]


def test_a_policy_limit_on_its_own_is_a_cap(project, analyst):
    version = promote(
        incomplete(project, analyst, **{"Policy limit": "2000000.00"}),
        name="Policy limit",
        actor=analyst,
    )
    flagged = version.source_lineage["financial_structure"]["possibly_overstated"]
    assert flagged["uncapped"] == []
    assert flagged["attachment_read_as_zero"] == ["F-MULTI"]


def test_an_account_the_policies_sheet_omits_is_written_with_no_terms(project, analyst):
    version = promote(incomplete(project, analyst, row=False), name="No row", actor=analyst)

    multi = [row for row in rows(version, "oed_account") if row["AccNumber"] == "F-MULTI"]
    assert len(multi) == 1
    assert multi[0]["PolNumber"] == "F-MULTI"
    assert "_no_policy_row" not in multi[0]
    flagged = version.source_lineage["financial_structure"]["possibly_overstated"]
    assert flagged["no_policy_row"] == ["F-MULTI"]


def test_a_book_with_no_policies_rows_stays_ground_up(project, analyst):
    """No account file of blanks: that would claim an insured view nobody stated."""
    version = promote(
        imported(project, analyst, risks=self_valued_risks(), policies=[], contracts=[], scope=[]),
        name="Ground-up book",
        actor=analyst,
    )
    assert rows(version, "oed_account") == []
    assert perspectives(version) == {str(Perspective.GROUND_UP)}
    assert "no Policies rows" in version.source_lineage["financial_structure"]["reason"]


def test_ground_up_only_writes_no_terms_even_where_they_are_complete(project, analyst):
    version = promote(
        imported(project, analyst),
        name="Ground-up",
        policy_terms=PolicyTerms.GROUND_UP,
        actor=analyst,
    )
    assert rows(version, "oed_account") == []
    assert "LocDed6All" not in rows(version, "oed_location")[0]
    assert perspectives(version) == {str(Perspective.GROUND_UP)}


def test_a_contract_covering_only_accounts_outside_the_selection_is_named_not_written(
    project, analyst
):
    """A coarse geocode keeps F-COARSE out of cohort A, and the quota share with it."""
    base = financial_template()
    risks = [
        *base[0],
        risk_row("F-COARSE", "1", "-6.300000", "106.900000", total="500000.00",
                 precision="locality"),
    ]
    policies = [
        *base[1],
        layer_row("F-COARSE", "P-9", 1, attachment="0", limit="500000.00"),
    ]
    scope = [{"Contract number": "1", "Policy ID": "F-COARSE"}, {"Contract number": "2"}]
    batch = imported(project, analyst, risks=risks, policies=policies, scope=scope)

    version = promote(batch, name="Unscoped", actor=analyst)

    assert {row["ReinsNumber"] for row in rows(version, "oed_reins_info")} == {"2"}
    record = version.source_lineage["financial_structure"]
    assert record["contracts_written"] == [2]
    assert "Contract(s) 1 were left out" in record["reinsurance_note"]
    assert record["scope_rows_outside_version"] == 1


# -- contracts are held to the screen's rules ----------------------------------------------------

def test_a_contract_the_screen_would_refuse_is_reported_at_import(project, analyst):
    contracts = [contract_row(2, 1, "CXL", 1, **{"Attachment per event": "100000.00"})]
    batch = imported(project, analyst, contracts=contracts, scope=[{"Contract number": "2"}])

    refused = [item for item in batch.findings if item["code"] == "contract_refused"]
    assert refused
    assert "occurrence limit" in refused[0]["message"]
    assert batch.intake_report["financial_structure"]["contract_layers_refused"] == 1


def test_a_contract_the_screen_would_refuse_stops_the_promotion(project, analyst):
    contracts = [contract_row(2, 1, "CXL", 1, **{"Attachment per event": "100000.00"})]
    batch = imported(project, analyst, contracts=contracts, scope=[{"Contract number": "2"}])

    with pytest.raises(PromotionError, match="Contract 2 layer 1"):
        promote(batch, name="Refused", actor=analyst)


def test_a_contract_type_the_engine_is_not_given_is_refused_in_the_screens_words(
    project, analyst
):
    contracts = [contract_row(3, 1, "PR", 1, **{"Limit per risk": "100000.00"})]
    batch = imported(project, analyst, contracts=contracts, scope=[{"Contract number": "3"}])
    refused = [item for item in batch.findings if item["code"] == "contract_refused"]
    assert "not a contract type CASS applies in a run" in refused[0]["message"]


def test_the_import_report_counts_what_promotion_can_write(project, analyst):
    report = incomplete(project, analyst).intake_report["financial_structure"]
    assert report["policy_ids"] == 3
    assert report["policy_ids_with_terms"] == 2
    assert report["policy_ids_without_terms"] == 1
    assert report["contracts"] == 2
    assert report["contract_layers"] == 3


# -- through the API ---------------------------------------------------------------------------

def test_the_catalogue_offers_the_policy_terms_choices(api):
    response = api.get(f"{API}/assumptions/")
    assert response.status_code == 200
    offered = {item["value"]: item for item in response.json()["policy_terms"]}
    assert set(offered) == {"apply", "ground_up"}
    assert offered["apply"]["default"] is True


def test_the_choice_is_taken_through_the_api(api, project, analyst):
    batch = incomplete(project, analyst)
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {"name": "Via API", "policy_terms": "ground_up"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["financial_structure"]["applied"] is False


def test_an_unknown_choice_is_refused(api, project, analyst):
    batch = incomplete(project, analyst)
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {"name": "Nope", "policy_terms": "sometimes"},
        format="json",
    )
    assert response.status_code == 400
