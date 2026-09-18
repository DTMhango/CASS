"""Portfolio intake for CASS: the template, the reader, and the rules that read it.

CASS accepts one format. It is a workbook a person fills in, at one row per
risk, whose every column is bound to an OED field underneath -- so a schedule
written in the language the business uses lands in the language the model uses,
without anyone holding both in their head. :mod:`cass_extract.profile` is that
binding, :mod:`cass_extract.template` generates the workbook from it, and
:mod:`cass_extract.intake` reads a completed one back.

What comes out is canonical records rather than spreadsheet rows, which is why
the rest of the package -- cohorts, allocation, component splits, occupancy --
never mentions a column name. A loading API that populates CASS straight from a
source system produces the same records and gets the same rules.

Nothing here touches Django or the artifact store. The importer in the exposure
workspace supplies bytes and stores what comes back.

Two modules are the exception, and say so in their names.
:mod:`cass_extract.legacy_schema` and :mod:`cass_extract.legacy_reader` describe
the retired two-sheet extract, and exist only so :mod:`cass_extract.legacy` can
convert the existing book into the template once.
"""

from .allocation import (
    ALLOCATION_RULE_VERSION,
    PRIMARY_SHARE,
    AllocationError,
    AllocationEvidence,
    AllocationMethod,
    AllocationResult,
    LocationShare,
    PolicyAllocation,
    allocate,
    allocate_policy,
    apportion,
    concentration_envelope,
)
from .cohorts import (
    COHORT_RULE_VERSION,
    Assignment,
    Cohort,
    CountryScreen,
    Placement,
    assign,
    assign_all,
    business_complete,
    cohort_profile,
)
from .components import (
    BUILDING_ONLY,
    COMPONENT_RULE_VERSION,
    COVERAGE_ORDER,
    DEFAULT_SPLIT,
    PRESETS,
    ComponentSplit,
    Coverage,
    custom,
    preset,
    reconciliation,
    split_locations,
)
from .intake import (
    COVERAGE_COLUMNS,
    PARSER_VERSION,
    RISK_TOTAL_COLUMN,
    IntakeError,
    IntakeRead,
    coverage_evidence,
    cross_check,
    defers_to_allocation,
    policy_record,
    read_rows,
    read_workbook,
    records,
    risk_record,
    states_coverages,
    states_risk_total,
)
from .occupancy import (
    COMMERCIAL_GENERAL,
    DEFAULT_OCCUPANCY,
    MIXED_COMMERCIAL,
    NOT_REPORTED,
    OCCUPANCY_PRESETS,
    OCCUPANCY_RULE_VERSION,
    UNKNOWN_CONSTRUCTION,
    UNKNOWN_OCCUPANCY,
    OccupancyAssumption,
    TaxonomyClass,
    assign_taxonomy,
    occupancy_preset,
    taxonomy_record,
    uniform,
)
from .profile import (
    POLICY_SHEET,
    PROFILE_NAME,
    PROFILE_VERSION,
    RISK_SHEET,
    Column,
    ProfileError,
    Sheet,
    WhenBlank,
)
from .records import ExtractReadError, Finding, SheetRead, SourceRow
from .template import workbook as intake_workbook

__all__ = [
    "ALLOCATION_RULE_VERSION",
    "BUILDING_ONLY",
    "COMMERCIAL_GENERAL",
    "COHORT_RULE_VERSION",
    "COMPONENT_RULE_VERSION",
    "COVERAGE_COLUMNS",
    "COVERAGE_ORDER",
    "DEFAULT_OCCUPANCY",
    "DEFAULT_SPLIT",
    "MIXED_COMMERCIAL",
    "NOT_REPORTED",
    "OCCUPANCY_PRESETS",
    "OCCUPANCY_RULE_VERSION",
    "PARSER_VERSION",
    "POLICY_SHEET",
    "PRESETS",
    "PRIMARY_SHARE",
    "PROFILE_NAME",
    "PROFILE_VERSION",
    "RISK_SHEET",
    "RISK_TOTAL_COLUMN",
    "UNKNOWN_CONSTRUCTION",
    "UNKNOWN_OCCUPANCY",
    "AllocationError",
    "AllocationEvidence",
    "AllocationMethod",
    "AllocationResult",
    "Assignment",
    "Cohort",
    "Column",
    "ComponentSplit",
    "CountryScreen",
    "Coverage",
    "ExtractReadError",
    "Finding",
    "IntakeError",
    "IntakeRead",
    "LocationShare",
    "OccupancyAssumption",
    "Placement",
    "PolicyAllocation",
    "ProfileError",
    "Sheet",
    "SheetRead",
    "SourceRow",
    "TaxonomyClass",
    "WhenBlank",
    "allocate",
    "allocate_policy",
    "apportion",
    "assign",
    "assign_all",
    "assign_taxonomy",
    "business_complete",
    "cohort_profile",
    "concentration_envelope",
    "coverage_evidence",
    "cross_check",
    "custom",
    "defers_to_allocation",
    "intake_workbook",
    "occupancy_preset",
    "policy_record",
    "preset",
    "read_rows",
    "read_workbook",
    "reconciliation",
    "records",
    "risk_record",
    "split_locations",
    "states_coverages",
    "states_risk_total",
    "taxonomy_record",
    "uniform",
]
