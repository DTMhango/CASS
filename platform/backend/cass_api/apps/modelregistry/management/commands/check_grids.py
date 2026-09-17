"""Check registered grids for cells that share ground.

The grid builder now refuses a refinement whose edges are off the base lattice,
or whose resolution the base does not divide, because either puts some places in
two cells and others in none. Grids built before the refusal may carry exactly
that, and nothing about a registered grid would say so: a location in an overlap
simply maps to whichever cell the lookup finds first.

Run against every grid, or one::

    python manage.py check_grids
    python manage.py check_grids --country PH --version 0.1.0

It changes nothing. A grid that overlaps is a grid to rebuild as a new version,
and its hazard with it, which is a decision rather than a repair.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.modelregistry.assets import ModelAssetError, load_grid
from apps.modelregistry.models import AreaPerilGrid
from cass_keys import grids


class Command(BaseCommand):
    help = "Report registered grids whose cells overlap."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--country", help="Only this country's grids.")
        parser.add_argument("--grid-version", help="Only this version.")

    def handle(self, *args, **options) -> None:
        found = AreaPerilGrid.objects.order_by("country_code", "version")
        if options.get("country"):
            found = found.filter(country_code=options["country"].upper())
        if options.get("grid_version"):
            found = found.filter(version=options["grid_version"])
        if not found.exists():
            raise CommandError("No registered grid matches.")

        overlapping = 0
        for grid in found:
            try:
                cells = load_grid(grid).cells
            except ModelAssetError as exc:
                self.stdout.write(f"{grid.reference}: not checked. {exc}")
                continue
            report = grids.overlaps(cells)
            if not report["checked"]:
                self.stdout.write(
                    f"{grid.reference}: not checked. Its cells sit on a lattice too "
                    "fine to count in one pass."
                )
            elif report["overlapping_cells"]:
                overlapping += 1
                self.stdout.write(
                    f"{grid.reference}: {report['overlapping_cells']:,} of "
                    f"{report['cells']:,} cells share ground with another cell. "
                    "Rebuild it as a new version from an aligned specification."
                )
            else:
                self.stdout.write(
                    f"{grid.reference}: {report['cells']:,} cells, none overlapping."
                )
        if overlapping:
            self.stdout.write(f"{overlapping} grid(s) overlap.")
