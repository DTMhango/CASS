"""Reading a portfolio's financial structure.

Section 3 asks the financial structure workspace to show accounts and layers,
contracts, a scope preview, the inuring order and the reconciliation between
them. These hold the reading to one rule: it states what the files say and
what does not add up, and repairs nothing.

That rule is the same one section 8 applies to reported exposure. A layer with
no limit, a contract whose scope reaches no location, insured value no treaty
covers -- each is a fact about the book somebody has to decide about. A reader
that closed a layer gap or spread a contract over the whole portfolio would
produce a ceded loss nobody could tie back to a treaty.
"""

from __future__ import annotations

from decimal import Decimal

from cass_oed.reader import read_bytes
from cass_oed.schema import FileKind
from cass_oed.structure import read
from cass_oed.validation import PortfolioFiles

LOCATIONS = (
    b"PortNumber,AccNumber,LocNumber,CountryCode,Latitude,Longitude,"
    b"OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,LocCurrency\n"
    b"1,ACC-1,LOC-1,ID,-6.2088,106.8456,1100,5000,QEQ,4000000,1000000,USD\n"
    b"1,ACC-1,LOC-2,ID,-6.9175,107.6191,1200,5000,QEQ,2000000,500000,USD\n"
    b"1,ACC-2,LOC-3,ID,-7.2575,112.7521,1050,3000,QEQ,1000000,500000,USD\n"
)

ACCOUNTS = (
    b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,LayerNumber,"
    b"LayerParticipation,LayerLimit,LayerAttachment,PolDed6All,PolLimit6All,PolPeril\n"
    b"1,ACC-1,USD,POL-1,QEQ,1,0.5,5000000,1000000,50000,6000000,QEQ\n"
    b"1,ACC-1,USD,POL-1,QEQ,2,0.25,10000000,6000000,50000,6000000,QEQ\n"
)

CONTRACTS = (
    b"ReinsNumber,ReinsLayerNumber,ReinsName,ReinsPeril,CededPercent,RiskLimit,"
    b"RiskAttachment,OccLimit,OccAttachment,PlacedPercent,ReinsCurrency,"
    b"InuringPriority,ReinsType\n"
    b"1,1,Cat XL layer 1,QEQ,0.9,,,5000000,2000000,1.0,USD,1,CXL\n"
    b"2,1,Whole account QS,QEQ,0.3,,,,,1.0,USD,2,QS\n"
)

SCOPE = (
    b"ReinsNumber,PortNumber,AccNumber,PolNumber,LocGroup,LocNumber,CededPercent\n"
    b"1,1,,,,,0.9\n"
    b"2,1,,,,LOC-1,0.3\n"
)


def files(*, accounts=ACCOUNTS, contracts=CONTRACTS, scope=SCOPE) -> PortfolioFiles:
    return PortfolioFiles(
        location=read_bytes(FileKind.LOCATION, LOCATIONS),
        account=read_bytes(FileKind.ACCOUNT, accounts) if accounts else None,
        reins_info=read_bytes(FileKind.REINS_INFO, contracts) if contracts else None,
        reins_scope=read_bytes(FileKind.REINS_SCOPE, scope) if scope else None,
    )


def test_the_layers_of_a_policy_are_read_in_the_order_they_apply():
    structure = read(files())

    assert [layer.layer_number for layer in structure.layers] == [1, 2]
    assert structure.layers[0].attachment == Decimal("1000000")
    assert structure.layers[0].limit == Decimal("5000000")
    assert structure.layers[0].participation == Decimal("0.5")
    assert structure.layers[1].attachment == Decimal("6000000")


def test_a_contract_carries_its_type_terms_and_inuring_priority():
    structure = read(files())
    cat, quota = structure.contracts

    assert cat.contract_type == "CXL"
    assert cat.occurrence_limit == Decimal("5000000")
    assert cat.occurrence_attachment == Decimal("2000000")
    assert cat.inuring_priority == 1
    assert quota.contract_type == "QS"
    assert quota.ceded_percent == Decimal("0.3")


def test_the_inuring_order_says_which_contracts_run_together():
    """Lower priorities inure to the benefit of higher ones."""
    structure = read(files())

    assert structure.inuring_order == ((1, (1,)), (2, (2,)))


def test_a_scope_row_naming_no_location_reaches_the_whole_portfolio():
    structure = read(files())
    cat = structure.contracts[0]

    assert cat.locations_reached == 3
    assert cat.scope_tiv == Decimal("9000000")


def test_a_scope_row_naming_one_location_reaches_only_that_one():
    structure = read(files())
    quota = structure.contracts[1]

    assert quota.locations_reached == 1
    assert quota.scope_tiv == Decimal("5000000")


def test_value_no_contract_reaches_is_reported_as_retained():
    """A location outside every treaty keeps its loss, and a reader must see that."""
    structure = read(files(scope=b"ReinsNumber,PortNumber,AccNumber,PolNumber,"
                           b"LocGroup,LocNumber,CededPercent\n1,1,,,,LOC-1,0.9\n"))

    assert structure.uncovered_locations == 2
    assert structure.uncovered_tiv == Decimal("4000000")
    assert any(
        finding.code == "locations_outside_every_contract"
        for finding in structure.findings
    )


def test_a_contract_no_scope_row_names_is_blocking():
    structure = read(files(scope=b"ReinsNumber,PortNumber,AccNumber,PolNumber,"
                           b"LocGroup,LocNumber,CededPercent\n1,1,,,,,0.9\n"))

    finding = next(
        item for item in structure.findings if item.code == "contract_without_scope"
    )
    assert finding.blocking is True
    assert "Contract 2" in finding.subject


def test_a_scope_naming_a_location_the_book_does_not_hold_is_blocking():
    structure = read(files(scope=b"ReinsNumber,PortNumber,AccNumber,PolNumber,"
                           b"LocGroup,LocNumber,CededPercent\n"
                           b"1,1,,,,NOT-A-LOCATION,0.9\n2,1,,,,LOC-1,0.3\n"))

    finding = next(
        item for item in structure.findings if item.code == "scope_matches_no_location"
    )
    assert finding.blocking is True


def test_a_layer_with_no_limit_is_reported_rather_than_assumed_unlimited():
    structure = read(
        files(
            accounts=(
                b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,"
                b"LayerNumber,LayerParticipation,PolPeril\n"
                b"1,ACC-1,USD,POL-1,QEQ,1,0.5,QEQ\n"
            )
        )
    )

    assert any(finding.code == "no_limit" for finding in structure.findings)


def test_a_signed_share_above_the_whole_layer_is_blocking():
    structure = read(
        files(
            accounts=(
                b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,"
                b"LayerNumber,LayerParticipation,LayerLimit,LayerAttachment,PolPeril\n"
                b"1,ACC-1,USD,POL-1,QEQ,1,1.5,5000000,1000000,QEQ\n"
            )
        )
    )

    finding = next(
        item for item in structure.findings if item.code == "participation_above_one"
    )
    assert finding.blocking is True


def test_a_gap_in_the_layers_is_a_band_with_no_term_written_for_it():
    structure = read(
        files(
            accounts=(
                b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,"
                b"LayerNumber,LayerParticipation,LayerLimit,LayerAttachment,PolPeril\n"
                b"1,ACC-1,USD,POL-1,QEQ,1,0.5,5000000,1000000,QEQ\n"
                b"1,ACC-1,USD,POL-1,QEQ,3,0.5,5000000,9000000,QEQ\n"
            )
        )
    )

    assert any(finding.code == "layer_gap" for finding in structure.findings)


def test_a_per_risk_contract_says_the_engine_will_not_apply_it():
    """ADR 10: the patched worker runs reinsurance at portfolio level only."""
    structure = read(
        files(
            contracts=(
                b"ReinsNumber,ReinsLayerNumber,ReinsName,ReinsPeril,CededPercent,"
                b"RiskLimit,RiskAttachment,OccLimit,OccAttachment,PlacedPercent,"
                b"ReinsCurrency,InuringPriority,ReinsType\n"
                b"1,1,Per risk,QEQ,0.5,1000000,500000,,,1.0,USD,1,PR\n"
            ),
            scope=b"ReinsNumber,PortNumber,AccNumber,PolNumber,LocGroup,LocNumber,"
                  b"CededPercent\n1,1,,,,,0.5\n",
        )
    )
    contract = structure.contracts[0]

    assert contract.applied_by_the_engine is False
    assert any("portfolio level" in note for note in contract.notes)


def test_a_portfolio_with_no_financial_files_reads_as_having_no_structure():
    structure = read(files(accounts=None, contracts=None, scope=None))

    assert structure.layers == ()
    assert structure.contracts == ()
    assert structure.total_tiv == Decimal("9000000")
    # Nothing is uncovered where nothing was ever meant to cover it.
    assert structure.uncovered_locations == 0
