"""Seed a demonstration workspace.

Milestone M1 in section 13 is signing in, creating or importing a small
portfolio, previewing valid OED and watching a durable task. This command
produces everything needed to walk that journey on a clean installation, so a
reviewer can see the product rather than a login screen.

It is idempotent: running it twice leaves the same state, not a second copy.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import PlatformRole, User
from apps.exposure import services as exposure_services
from apps.exposure.models import ExposureVersion
from apps.modelregistry.models import (
    AreaPerilGrid,
    AssumptionSet,
    IntensityMeasure,
    ModelVersion,
    Peril,
    PublicationState,
    VulnerabilitySet,
)
from apps.projects.models import Project, ProjectMembership, ProjectRole
from kre_oed.schema import FileKind

# .../platform/backend/kre_api/apps/projects/management/commands/seed_demo.py
# parents[6] is platform/, which holds the shared cross-service fixtures.
FIXTURES = Path(__file__).resolve().parents[6] / "tests" / "fixtures" / "piwind"

DEMO_PASSWORD = "kre-demo-password"

USERS = [
    ("analyst", "Ada", "Analyst", PlatformRole.ANALYST),
    ("modeller", "Mo", "Modeller", PlatformRole.MODELLER),
    ("reviewer", "Rhea", "Reviewer", PlatformRole.REVIEWER),
]

#: A small Indonesian earthquake portfolio that passes validation, so the
#: journey can be walked to publication.
EARTHQUAKE_LOCATIONS = """PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,LocCurrency
1,ACC-1,LOC-1,1,ID,-6.2088,106.8456,1100,5000,QEQ,4500000,900000,IDR
1,ACC-1,LOC-2,1,ID,-6.9175,107.6191,1200,5000,QEQ,2750000,400000,IDR
1,ACC-1,LOC-3,1,ID,-7.2575,112.7521,1050,3000,QEQ,1200000,150000,IDR
1,ACC-1,LOC-4,1,ID,-8.6500,115.2167,1150,5000,QQ,3100000,620000,IDR
"""


class Command(BaseCommand):
    help = "Create a demonstration project, model version and portfolio."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEMO_PASSWORD,
            help="Password for the seeded users. Development use only.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]

        users = self._seed_users(password)
        project = self._seed_project(users["analyst"])
        model_version = self._seed_model(users["modeller"])
        self._seed_assumption_sets(users["modeller"])
        exposure = self._seed_exposure(project, users["analyst"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demonstration workspace ready."))
        self.stdout.write(f"  Project           {project.reference}")
        self.stdout.write(f"  Model version     {model_version.reference}")
        self.stdout.write(
            f"  Portfolio         {exposure.name} v{exposure.version} ({exposure.state})"
        )
        self.stdout.write(f"  Total insured     {exposure.total_tiv} {exposure.run_currency}")
        self.stdout.write("")
        self.stdout.write("  Sign in as analyst, modeller or reviewer.")
        self.stdout.write(f"  Password: {password}")

        blockers = model_version.publication_blockers()
        if blockers:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "The model version is published as a research prototype. Outstanding:"
                )
            )
            for blocker in blockers:
                self.stdout.write(f"    - {blocker}")

    # -- steps -------------------------------------------------------------
    def _seed_users(self, password: str) -> dict[str, User]:
        users: dict[str, User] = {}
        for username, first, last, role in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "platform_role": role,
                    "email": f"{username}@example.invalid",
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            users[username] = user
            self.stdout.write(f"{'created' if created else 'found'} user {username}")
        return users

    def _seed_project(self, owner: User) -> Project:
        project, created = Project.objects.get_or_create(
            reference="idn-fac-2026",
            defaults={
                "name": "Indonesia facultative 2026",
                "purpose": "Pilot earthquake portfolio analysis for the internal release.",
                "team": "Catastrophe modelling",
                "created_by": owner,
            },
        )
        ProjectMembership.objects.get_or_create(
            project=project, user=owner, defaults={"role": ProjectRole.OWNER}
        )
        self.stdout.write(f"{'created' if created else 'found'} project {project.reference}")
        return project

    def _seed_model(self, modeller: User) -> ModelVersion:
        grid, _ = AreaPerilGrid.objects.get_or_create(
            country_code="ID",
            version="0.1.0",
            defaults={
                "label": "Indonesia adaptive prototype grid",
                "base_resolution_deg": "0.100000",
                "refined_resolution_deg": "0.025000",
                "refinement_rule": (
                    "Refined over Jakarta, Bandung, Surabaya and Denpasar; coarse "
                    "elsewhere; ocean cells excluded."
                ),
                "cell_count": 0,
                "excludes_offshore": True,
                "site_condition_source": "Not yet acquired; Vs30 treatment is outstanding.",
                "site_condition_fallback": "No approved fallback hierarchy yet.",
                "border_policy": "Locations beyond the domain are reported, never snapped.",
                "mapping_tolerance_km": "0.00",
                "publication_state": PublicationState.DRAFT,
                "notes": "Prototype specification for the phase 3 grid design spike.",
                "created_by": modeller,
            },
        )

        vulnerability, _ = VulnerabilitySet.objects.get_or_create(
            country_code="ID",
            version="2026.0.0",
            defaults={
                "source": "GEM Global Vulnerability Model v2026.0.0",
                "source_commit": "5974372ac3f4a99f25d0649eb030fbe596f23b36",
                "taxonomy_generation": "2026",
                "licence": "CC BY-NC-SA 4.0",
                # Section 10: commercial use is unconfirmed, so this stays false
                # and blocks full publication until GEM confirms in writing.
                "licence_cleared": False,
                "licence_note": (
                    "Public GEM models are CC BY-NC-SA. KRE use supports commercial "
                    "reinsurance decisions and remains a research activity until GEM "
                    "confirms permitted commercial use in writing."
                ),
                "function_count": 32,
                # Indonesia has 17 PGA-based and 15 SA-based functions per
                # coverage component; the PGA demand is what blocks publication
                # as a full country model.
                "imts_used": ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"],
                "coverage_components": ["structural", "nonstructural", "contents"],
                "damage_bin_count": 0,
                "publication_state": PublicationState.DRAFT,
                "created_by": modeller,
            },
        )

        model_version, created = ModelVersion.objects.get_or_create(
            country_code="ID",
            peril=Peril.EARTHQUAKE,
            version="0.1.0-sa",
            defaults={
                "label": "Indonesia earthquake, SA-only prototype",
                "grid": grid,
                "vulnerability_set": vulnerability,
                "hazard_source_model": "GEM Indonesia source model (release to be pinned)",
                "openquake_version": "3.23.1",
                "oasis_version": "2.5.7",
                "converter_version": "0.1.0",
                "oed_schema_version": "4.0.0",
                "imts": [
                    IntensityMeasure.SA_03,
                    IntensityMeasure.SA_06,
                    IntensityMeasure.SA_10,
                ],
                "peril_scope": {
                    "QEQ": {
                        "treatment": "included",
                        "rationale": "Primary ground shaking on the approved grid.",
                    },
                    "QLF": {
                        "treatment": "excluded",
                        "rationale": "Liquefaction is not modelled in this release.",
                        "expected_bias": "Understates loss on soft coastal soils.",
                    },
                    "QLS": {
                        "treatment": "excluded",
                        "rationale": "Earthquake-induced landslide is not modelled.",
                        "expected_bias": "Understates loss in steep terrain.",
                    },
                    "QTS": {
                        "treatment": "excluded",
                        "rationale": "Tsunami is not modelled in this release.",
                        "expected_bias": "Materially understates coastal loss.",
                    },
                    "QFF": {
                        "treatment": "excluded",
                        "rationale": "Fire following earthquake is not modelled.",
                        "expected_bias": "Understates dense urban loss.",
                    },
                },
                "known_limitations": (
                    "SA-only research prototype. PGA-based vulnerability classes are "
                    "unmodelled, secondary perils are excluded, and business "
                    "interruption is not modelled."
                ),
                "unsupported_taxonomy_report": {
                    "classes": ["PGA-based taxonomies (17 of 32 Indonesian functions)"],
                    "unsupported_tiv_share": 0.0,
                    "note": (
                        "The share is measured against a benchmark portfolio once "
                        "one is approved; until then it is unquantified, not zero."
                    ),
                },
                "is_research_prototype": True,
                "publication_state": PublicationState.PUBLISHED,
                "created_by": modeller,
            },
        )
        self.stdout.write(
            f"{'created' if created else 'found'} model version {model_version.reference}"
        )
        return model_version

    def _seed_assumption_sets(self, modeller: User) -> None:
        """Section 8 requires at least three approved, versioned sets."""
        for flavour, label in (
            (AssumptionSet.Flavour.BASELINE, "Baseline conditional priors"),
            (AssumptionSet.Flavour.MORE_ROBUST, "More robust construction mix"),
            (AssumptionSet.Flavour.MORE_VULNERABLE, "More vulnerable construction mix"),
        ):
            AssumptionSet.objects.get_or_create(
                country_code="ID",
                flavour=flavour,
                version="0.1.0",
                defaults={
                    "label": label,
                    "segment": "Facultative commercial and industrial",
                    "rules": {},
                    "provenance": (
                        "Placeholder. Priors are derived from GEM exposure once the "
                        "licensed spatial data is obtained and calibrated against the "
                        "KRE portfolio."
                    ),
                    "weighting_basis": "replacement_cost",
                    "publication_state": PublicationState.DRAFT,
                    "created_by": modeller,
                },
            )
        self.stdout.write("seeded the three assumption sets")

    def _seed_exposure(self, project: Project, analyst: User) -> ExposureVersion:
        existing = ExposureVersion.objects.filter(
            project=project, name="Indonesia pilot portfolio"
        ).first()
        if existing:
            self.stdout.write("found the demonstration portfolio")
            return existing

        exposure = ExposureVersion.objects.create(
            project=project,
            name="Indonesia pilot portfolio",
            version=1,
            cedant="Demonstration Cedant",
            valuation_date=dt.date(2026, 6, 30),
            source_description="Seeded by seed_demo for the M1 walkthrough.",
            created_by=analyst,
            updated_by=analyst,
        )
        exposure_services.attach_file(
            exposure,
            FileKind.LOCATION,
            EARTHQUAKE_LOCATIONS.encode("utf-8"),
            filename="indonesia_pilot_locations.csv",
            actor=analyst,
        )
        exposure_services.run_validation(exposure, actor=analyst)
        self.stdout.write("created and validated the demonstration portfolio")

        self._seed_piwind(project, analyst)
        return exposure

    def _seed_piwind(self, project: Project, analyst: User) -> None:
        """The official PiWind portfolio, as the section 12 regression baseline."""
        if not FIXTURES.is_dir():
            self.stdout.write(
                self.style.WARNING(f"PiWind fixtures not found at {FIXTURES}; skipping")
            )
            return
        if ExposureVersion.objects.filter(project=project, name="PiWind baseline").exists():
            return

        exposure = ExposureVersion.objects.create(
            project=project,
            name="PiWind baseline",
            version=1,
            cedant="Oasis PiWind reference model",
            source_description="Official OasisLMF PiWind exposure, used as the regression baseline.",
            created_by=analyst,
            updated_by=analyst,
        )
        for kind, filename in (
            (FileKind.LOCATION, "SourceLocOEDPiWind10.csv"),
            (FileKind.ACCOUNT, "SourceAccOEDPiWind.csv"),
            (FileKind.REINS_INFO, "SourceReinsInfoOEDPiWind.csv"),
            (FileKind.REINS_SCOPE, "SourceReinsScopeOEDPiWind.csv"),
        ):
            path = FIXTURES / filename
            if not path.is_file():
                raise CommandError(f"missing PiWind fixture {path}")
            exposure_services.attach_file(
                exposure, kind, path.read_bytes(), filename=filename, actor=analyst
            )
        exposure_services.run_validation(exposure, actor=analyst)
        self.stdout.write("created and validated the PiWind baseline portfolio")
