"""The Klapton Re geocoded policy extract.

A two-sheet workbook of Klapton Reinsurance's own book, which is not OED and is
not a model asset. It gets its own package because it is a source format with
its own contract: its own schema and version, its own confidentiality rules,
its own coordinate-eligibility cohorts, and a join whose failure modes are
specific to it.

Nothing here touches Django or the artifact store. The importer in the exposure
workspace supplies bytes and stores what comes back.
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
    concentration_envelope,
)
from .cohorts import (
    COHORT_RULE_VERSION,
    Assignment,
    Cohort,
    assign,
    assign_all,
    business_complete,
    profile,
)
from .join import JOIN_RULE_VERSION, JoinFinding, JoinReport, JoinSeverity
from .join import build as build_join_report
from .reader import (
    ExtractRead,
    ExtractReadError,
    Finding,
    SheetRead,
    SourceRow,
    masked,
    read_rows,
    read_workbook,
)
from .schema import (
    CONFIDENTIAL_COLUMNS,
    LOCATION_FIELDS,
    LOCATION_SHEET,
    PARSER_VERSION,
    POLICY_FIELDS,
    POLICY_SHEET,
    PROFILE_NAME,
    SCHEMA_VERSION,
    DataType,
    FieldSpec,
    Sensitivity,
    country_code,
)

__all__ = [
    "ALLOCATION_RULE_VERSION",
    "COHORT_RULE_VERSION",
    "CONFIDENTIAL_COLUMNS",
    "JOIN_RULE_VERSION",
    "LOCATION_FIELDS",
    "LOCATION_SHEET",
    "PARSER_VERSION",
    "POLICY_FIELDS",
    "POLICY_SHEET",
    "PROFILE_NAME",
    "SCHEMA_VERSION",
    "AllocationError",
    "AllocationEvidence",
    "AllocationMethod",
    "AllocationResult",
    "Assignment",
    "Cohort",
    "DataType",
    "ExtractRead",
    "ExtractReadError",
    "FieldSpec",
    "Finding",
    "JoinFinding",
    "JoinReport",
    "JoinSeverity",
    "LocationShare",
    "PRIMARY_SHARE",
    "PolicyAllocation",
    "Sensitivity",
    "SheetRead",
    "SourceRow",
    "allocate",
    "allocate_policy",
    "assign",
    "assign_all",
    "build_join_report",
    "business_complete",
    "concentration_envelope",
    "country_code",
    "masked",
    "profile",
    "read_rows",
    "read_workbook",
]
