"""Close runs whose worker did not survive.

A run records its own progress, so a worker that is killed between two stages
leaves the run in RUNNING for ever: nothing is executing it and nothing will
ever move it. The monitor then shows work in progress that is not in progress,
which is the unintelligible state the monitor exists to prevent -- and it is
worse than a failure, because a failure says what to do next.

This closes those runs as failed, naming what happened. It is not a retry: a
retry is a new run, and this deliberately leaves the evidence of the abandoned
one in place.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.runs.models import Run
from cass_core.runs import RunState

SUMMARY = (
    "The worker running this did not survive, so nothing was left to finish it "
    "or to record why it stopped. Nothing in the run's evidence has been "
    "removed. Start a new run when the cause is understood."
)


class Command(BaseCommand):
    help = "Fail runs left running by a worker that died, older than a cut-off."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--older-than",
            type=int,
            default=60,
            metavar="MINUTES",
            help=(
                "Only touch runs that last changed longer ago than this, so a run "
                "that is genuinely working is never closed underneath it "
                "(default: 60)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be closed and change nothing.",
        )

    def handle(self, *args, **options) -> None:
        cutoff = timezone.now() - dt.timedelta(minutes=options["older_than"])
        stale = Run.objects.filter(
            state__in=[RunState.RUNNING.value, RunState.QUEUED.value],
            updated_at__lt=cutoff,
        ).order_by("created_at")

        if not stale:
            self.stdout.write("No stale runs.")
            return

        for run in stale:
            self.stdout.write(f"{run.id} {run.kind} {run.state} at {run.stage or 'no stage'}")
            if options["dry_run"]:
                continue
            progress = run.progress
            run.transition(
                RunState.FAILED,
                stage=run.stage or None,
                failure_summary=SUMMARY,
                failure_detail=f"Last updated {run.updated_at.isoformat()}.",
                save=False,
            )
            run.progress = progress
            run.save()

        verb = "would be closed" if options["dry_run"] else "closed"
        self.stdout.write(self.style.SUCCESS(f"{len(stale)} run(s) {verb}."))
