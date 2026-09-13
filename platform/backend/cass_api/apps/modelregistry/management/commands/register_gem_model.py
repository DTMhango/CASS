"""Build a pilot country's vulnerability set from GEM and register it.

Run against a local GEM release clone::

    python manage.py register_gem_model --root /models/gem/v2026.0.0 --country ID

The GEM data is licensed, large and outside version control, so the path is an
argument rather than a setting -- where it sits is a property of the machine
doing the build. Without it there is no vulnerability set and no model version,
which is the honest state of a fresh installation: the platform can hold
exposure and refuse to model it, and cannot invent a damage relationship.

A set is cleared under GEM Foundation's written permission for the data and
models it makes publicly available (ADR 15), and records that permission. A
different entitlement is stated with ``--licence-reference``, and
``--licence-cleared`` records it as a clearance, so whatever the registry holds
names the thing that grants it.
"""

from __future__ import annotations

import json
import pathlib

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.modelregistry import gem
from apps.modelregistry.pilot import PILOT_COUNTRIES


class Command(BaseCommand):
    help = "Build and register a pilot vulnerability set from a GEM release."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--root",
            required=True,
            help="GEM release root, holding global_vulnerability_model and "
            "global_exposure_model.",
        )
        parser.add_argument(
            "--country",
            action="append",
            dest="countries",
            help="Country code to build. Repeatable; defaults to every pilot country.",
        )
        parser.add_argument(
            "--licence-cleared",
            action="store_true",
            help="Record the entitlement named by --licence-reference as a clearance.",
        )
        parser.add_argument(
            "--licence-reference",
            default="",
            help="The agreement or approval that grants the clearance. Required "
            "with --licence-cleared.",
        )
        parser.add_argument(
            "--actor",
            default="",
            help="Email of the user to record as having registered this.",
        )
        parser.add_argument(
            "--vulnerability-only",
            action="store_true",
            help="Register the vulnerability set without assembling a model version.",
        )
        parser.add_argument(
            "--report",
            default="",
            help="Write the build report to this path as JSON.",
        )

    def handle(self, *args, **options) -> None:
        root = pathlib.Path(options["root"]).expanduser()
        countries = [
            item.upper() for item in (options["countries"] or PILOT_COUNTRIES)
        ]
        unknown = sorted(set(countries) - set(PILOT_COUNTRIES))
        if unknown:
            raise CommandError(
                f"No pilot grid exists for {', '.join(unknown)}. Available: "
                + ", ".join(PILOT_COUNTRIES)
                + "."
            )

        try:
            # Cleared by default, under GEM's written permission; the flags remain
            # for stating a different entitlement explicitly.
            licence = gem.LicenceStatement(
                cleared=options["licence_cleared"] or not options["licence_reference"],
                reference=options["licence_reference"] or gem.GEM_PERMISSION,
            )
        except gem.GemRegistrationError as exc:
            raise CommandError(str(exc)) from None

        actor = None
        if options["actor"]:
            actor = User.objects.filter(email__iexact=options["actor"]).first()
            if actor is None:
                raise CommandError(f"No user with email {options['actor']!r}.")

        reports = {}
        for code in countries:
            try:
                if options["vulnerability_only"]:
                    vulnerability_set, built = gem.register_vulnerability(
                        code, root=root, licence=licence, actor=actor
                    )
                    registered = str(vulnerability_set)
                else:
                    model = gem.register(
                        code, root=root, licence=licence, actor=actor
                    )
                    built = gem.build(code, root=root)
                    registered = str(model)
            except gem.GemRegistrationError as exc:
                raise CommandError(str(exc)) from None

            reports[code] = gem.report(built)
            spanning = reports[code]["multi_imt"]["classes_needing_multi_imt"]
            self.stdout.write(
                self.style.SUCCESS(f"Registered {registered}")
                + f"\n  {reports[code]['classes']} classes, "
                f"{reports[code]['functions']} functions"
                f"\n  {spanning} classes span intensity measures and are refused "
                "until the multi-IMT representation is approved"
                + (
                    ""
                    if licence.cleared
                    else "\n  Licence not cleared: research and platform "
                    "development only"
                )
            )

        if options["report"]:
            destination = pathlib.Path(options["report"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(reports, indent=2, sort_keys=True), encoding="utf-8"
            )
            self.stdout.write(f"Report written to {destination}")
