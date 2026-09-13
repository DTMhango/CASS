"""Register a published OED specification from a pinned ODS Tools release."""

from __future__ import annotations

import pathlib

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.standards import services
from apps.standards.registry import StandardError

#: How ODS Tools names the file for one release.
SPEC_FILENAME = "OpenExposureData_{version}Spec.json"


class Command(BaseCommand):
    help = "Register one or more OED specification versions from ODS Tools data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--root",
            required=True,
            help="The ODS Tools data directory holding OpenExposureData_*Spec.json.",
        )
        parser.add_argument(
            "--version",
            dest="versions",
            action="append",
            required=True,
            help="OED version to register. Repeatable.",
        )
        parser.add_argument(
            "--source",
            default="",
            help="The release these came from, such as 'ODS Tools 5.0.8'.",
        )
        parser.add_argument("--adopt", help="Version to make active after registering.")
        parser.add_argument("--actor", help="Email of the user to record.")

    def handle(self, *args, **options):
        root = pathlib.Path(options["root"])
        if not root.is_dir():
            raise CommandError(f"{root} is not a directory.")

        actor = None
        if options.get("actor"):
            actor = User.objects.filter(email=options["actor"]).first()
            if actor is None:
                raise CommandError(f"No user with email {options['actor']}.")

        source = options["source"] or f"ODS Tools data at {root}"
        registered = {}
        for version in options["versions"]:
            path = root / SPEC_FILENAME.format(version=version)
            try:
                record = services.register_from_path(
                    path, version=version, source=source, actor=actor
                )
            except (StandardError, services.StandardRegistrationError) as exc:
                raise CommandError(str(exc)) from exc
            registered[version] = record
            self.stdout.write(
                f"Registered {record} — {record.field_count} fields across "
                f"{len(record.file_kinds)} files"
            )

        if options.get("adopt"):
            wanted = options["adopt"]
            record = registered.get(wanted)
            if record is None:
                raise CommandError(
                    f"{wanted} was not registered in this run, so it cannot be adopted."
                )
            services.adopt(record, actor=actor)
            self.stdout.write(self.style.SUCCESS(f"{record} is now the active standard."))
