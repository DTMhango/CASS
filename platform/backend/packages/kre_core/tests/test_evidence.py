"""The evidence hierarchy and TIV allocation guarantees."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kre_core.evidence import (
    AllocationImbalance,
    Alternative,
    AttributeEvidence,
    EvidenceClass,
    EvidenceError,
    ReconciliationLine,
    ReportedValueProtected,
    WeightedAlternatives,
    allocate_value,
    apply_evidence,
    missingness_profile,
    reconcile,
)


def reported(attribute="occupancy", value="Office"):
    return AttributeEvidence(
        attribute=attribute,
        value=value,
        evidence=EvidenceClass.REPORTED,
        source_reference="cedant:SCHED-2026-04/row:12",
        confidence=1.0,
    )


def prior(attribute="occupancy", value="Industrial"):
    return AttributeEvidence(
        attribute=attribute,
        value=value,
        evidence=EvidenceClass.PRIOR,
        source_reference="gem:v2026.0.0/IDN/adm1",
        confidence=0.42,
        assumption_set_version="baseline-1.0.0",
    )


def test_assumption_may_not_overwrite_reported_data():
    """Section 15: assumed attributes overwriting reported data is a named risk."""
    with pytest.raises(ReportedValueProtected):
        apply_evidence(reported(), prior())


def test_assumption_fills_an_absent_attribute():
    assert apply_evidence(None, prior()).evidence is EvidenceClass.PRIOR


def test_stronger_evidence_supersedes_and_keeps_the_previous_value():
    current = prior()
    result = apply_evidence(current, reported())
    assert result.evidence is EvidenceClass.REPORTED
    assert result.superseded_value == "Industrial"


def test_override_requires_approval_and_retains_prior_value():
    override = AttributeEvidence(
        attribute="occupancy",
        value="Warehouse",
        evidence=EvidenceClass.OVERRIDE,
        source_reference="review:2026-09-11/analyst:dm",
        confidence=0.8,
        assumption_set_version="baseline-1.0.0",
        note="Site survey contradicts the schedule.",
    )
    with pytest.raises(ReportedValueProtected):
        apply_evidence(reported(), override)

    result = apply_evidence(reported(), override, allow_override=True)
    assert result.value == "Warehouse"
    assert result.superseded_value == "Office"


def test_assumption_must_name_an_assumption_set_version():
    with pytest.raises(EvidenceError):
        AttributeEvidence(
            attribute="height_class",
            value="mid-rise",
            evidence=EvidenceClass.PRIOR,
            source_reference="gem",
            confidence=0.5,
        )


def test_confidence_is_bounded():
    with pytest.raises(EvidenceError):
        AttributeEvidence(
            attribute="occupancy",
            value="Office",
            evidence=EvidenceClass.REPORTED,
            source_reference="cedant",
            confidence=1.4,
        )


def test_weights_must_sum_to_one():
    with pytest.raises(EvidenceError):
        WeightedAlternatives(
            attribute="construction",
            alternatives=(
                Alternative("C1", Decimal("0.5")),
                Alternative("W", Decimal("0.3")),
            ),
            assumption_set_version="baseline-1.0.0",
        )


def test_allocation_reconciles_exactly_to_the_source_value():
    """Section 15: weighted splitting must neither duplicate nor lose TIV."""
    alternatives = WeightedAlternatives(
        attribute="construction",
        alternatives=(
            Alternative("CR", Decimal("0.4")),
            Alternative("MUR", Decimal("0.35")),
            Alternative("W", Decimal("0.25")),
        ),
        assumption_set_version="baseline-1.0.0",
    )
    total = Decimal("1000000.01")
    allocated = alternatives.allocate(total)
    assert sum(share for _, share in allocated) == total


@pytest.mark.parametrize(
    "total,weights",
    [
        ("100.00", ["0.3333333", "0.3333333", "0.3333334"]),
        ("0.01", ["0.5", "0.5"]),
        ("7.77", ["0.1", "0.2", "0.3", "0.4"]),
        ("123456789.99", ["0.7", "0.3"]),
    ],
)
def test_allocation_never_leaks_value_under_rounding(total, weights):
    weighted = [(f"alt{i}", Decimal(w)) for i, w in enumerate(weights)]
    allocated = allocate_value(Decimal(total), weighted)
    assert sum(share for _, share in allocated) == Decimal(total)


def test_allocation_rejects_zero_total_weight():
    with pytest.raises(EvidenceError):
        allocate_value(Decimal("100"), [("a", Decimal(0))])


def test_reconciliation_reports_and_raises_on_imbalance():
    balanced = ReconciliationLine("portfolio", Decimal("100.00"), Decimal("100.00"))
    broken = ReconciliationLine("location:12", Decimal("50.00"), Decimal("49.99"))

    assert reconcile([balanced]) == []
    assert reconcile([balanced, broken], strict=False) == [broken]
    with pytest.raises(AllocationImbalance):
        reconcile([balanced, broken])


def test_missingness_profile_weights_gaps_by_value():
    records = [
        {"LocNumber": "1", "OccupancyCode": "1100", "BuildingTIV": "1000"},
        {"LocNumber": "2", "OccupancyCode": "", "BuildingTIV": "9000"},
    ]
    profile = missingness_profile(
        records, ["OccupancyCode", "ConstructionCode"], value_field="BuildingTIV"
    )

    occupancy = profile["OccupancyCode"]
    assert occupancy["present_count"] == 1
    assert occupancy["missing_count"] == 1
    assert occupancy["completeness"] == pytest.approx(0.5)
    # Half the records are missing, but nine tenths of the value is.
    assert occupancy["value_completeness"] == pytest.approx(0.1)

    construction = profile["ConstructionCode"]
    assert construction["completeness"] == pytest.approx(0.0)
