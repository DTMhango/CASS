"""Register an exported OpenQuake calculation as a hazard set.

Run against a directory of CSV exports::

    python manage.py register_hazard --export-dir /calc/out --country ID \\
        --version 0.1.0-prototype \\
        --source-model "CASS West Java prototype area source"

The source model is required and has no default. It is the largest single
determinant of the answer, nothing in the export records which one was used,
and a hazard set that could not say where its earthquakes came from would be
untraceable the moment anybody asked why a number moved.

``--attach`` points the country's model version at the registered set, which is
refused unless the set carries every intensity measure that version's
vulnerability functions demand.
"""

from __future__ import annotations

import json
import pathlib

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.modelregistry import hazard
from apps.modelregistry.models import ModelVersion, Peril


class Command(BaseCommand):
    help = "Register an OpenQuake event-based calculation as a hazard set."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--export-dir", required=True)
        parser.add_argument("--country", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument(
            "--source-model",
            required=True,
            help="The seismic source model behind the calculation.",
        )
        parser.add_argument("--source-licence", default="")
        parser.add_argument(
            "--source-cleared",
            action="store_true",
            help="Assert that use of the source model has been cleared.",
        )
        parser.add_argument(
            "--source-reference",
            default="",
            help="The approval granting the clearance. Required with --source-cleared.",
        )
        parser.add_argument(
            "--gmpe",
            action="append",
            dest="gmpes",
            help="A ground-motion model the logic tree used. Repeatable.",
        )
        parser.add_argument("--label", default="")
        parser.add_argument("--actor", default="")
        parser.add_argument(
            "--attach",
            action="store_true",
            help="Point the country's model version at this hazard set.",
        )
        parser.add_argument("--report", default="")

    def handle(self, *args, **options) -> None:
        directory = pathlib.Path(options["export_dir"]).expanduser()
        code = options["country"].upper()

        actor = None
        if options["actor"]:
            actor = User.objects.filter(email__iexact=options["actor"]).first()
            if actor is None:
                raise CommandError(f"No user with email {options['actor']!r}.")

        try:
            source = hazard.SourceStatement(
                model=options["source_model"],
                licence=options["source_licence"],
                cleared=options["source_cleared"],
                reference=options["source_reference"],
                ground_motion_models=tuple(options["gmpes"] or ()),
            )
            hazard_set, converted = hazard.register(
                directory,
                country_code=code,
                version=options["version"],
                source=source,
                label=options["label"],
                actor=actor,
            )
        except hazard.HazardRegistrationError as exc:
            raise CommandError(str(exc)) from None

        summary = hazard.report(hazard_set)
        self.stdout.write(
            self.style.SUCCESS(f"Registered {hazard_set.reference}")
            + f"\n  {summary['events']} events over "
            f"{summary['effective_time']:.0f} years "
            f"({summary['annual_event_rate']:.4f}/year)"
            f"\n  {summary['footprint_rows']:,} footprint rows over "
            f"{summary['cells']} cells"
            f"\n  measures: {', '.join(summary['imts'])}"
        )
        if converted.problems:
            self.stdout.write(self.style.WARNING("  problems found in conversion:"))
            for item in converted.problems:
                self.stdout.write(f"    - {item}")
        for item in summary["publication_blockers"]:
            self.stdout.write(self.style.WARNING(f"  blocks publication: {item}"))

        if options["attach"]:
            model = (
                ModelVersion.objects.filter(
                    country_code=code, peril=Peril.EARTHQUAKE
                )
                .order_by("-created_at")
                .first()
            )
            if model is None:
                raise CommandError(
                    f"No {code} model version exists to attach this to. Register a "
                    "vulnerability set first with register_gem_model."
                )
            try:
                hazard.attach(model, hazard_set, actor=actor)
            except hazard.HazardRegistrationError as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write(self.style.SUCCESS(f"Attached to {model}"))

        if options["report"]:
            destination = pathlib.Path(options["report"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
            )
            self.stdout.write(f"Report written to {destination}")
