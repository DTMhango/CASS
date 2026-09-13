"""Expire artifacts whose retention class has run out."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.artifacts import retention


class Command(BaseCommand):
    help = "Expire due artifacts, keeping their records for lineage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be expired and what would be kept, and change nothing.",
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="Consider at most this many."
        )

    def handle(self, *args, **options):
        report = retention.sweep(
            limit=options.get("limit"), dry_run=bool(options.get("dry_run"))
        )

        for item in report["expired"]:
            self.stdout.write(
                f"{'would expire' if report['dry_run'] else 'expired'} "
                f"{item['role'] or 'artifact'} ({item['retention']}): {item['uri']}"
            )
        for item in report["kept"]:
            self.stdout.write(
                self.style.WARNING(
                    f"kept {item['role'] or 'artifact'}: {item['reason']}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{report['expired_count']} expired, {report['kept_count']} kept, "
                f"{report['bytes_released']} bytes released"
            )
        )
