"""Operational metrics in the Prometheus text format.

Section 4 asks for metrics alongside logs and traces, and section 11 for
measured capacity; the platform exported nothing, so "is the queue backing up"
and "how many runs failed today" were questions answered by reading the
database by hand.

Everything here is read from the control plane's own records at scrape time
rather than counted in process memory. Two reasons. A worker restart would
zero an in-process counter and a graph would show a cliff that never happened;
and the records are already the source of truth for runs, artifacts and
results, so a second count kept beside them could only ever disagree with
them. No client library is needed to write the exposition format, so none is
added.

Nothing business-sensitive leaves: counts, states, profiles and bytes, never a
portfolio, a location or a loss.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from django.conf import settings
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: float, labels: dict[str, object] | None = None) -> str:
    if labels:
        rendered = ",".join(f'{key}="{_label(item)}"' for key, item in sorted(labels.items()))
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def _family(name: str, kind: str, help_text: str, samples: Iterable[str]) -> list[str]:
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", *samples]


def collect(*, now: dt.datetime | None = None) -> str:
    """Every metric, as one exposition document."""
    from apps.artifacts.models import Artifact
    from apps.audit.models import Approval
    from apps.results.models import ResultSet
    from apps.runs.admission import capacity
    from apps.runs.models import Run
    from cass_oed.schema import OED_SCHEMA_VERSION

    moment = now or timezone.now()
    lines: list[str] = []

    lines += _family(
        "cass_build_info",
        "gauge",
        "The installation's versions, as labels on a constant.",
        [_sample("cass_build_info", 1, {"api_version": "1.0.0", "oed_schema": OED_SCHEMA_VERSION})],
    )

    runs = Run.objects.values("kind", "state").annotate(total=Count("id"))
    lines += _family(
        "cass_runs",
        "gauge",
        "Runs by kind and lifecycle state.",
        [_sample("cass_runs", row["total"], {"kind": row["kind"], "state": row["state"]}) for row in runs],
    )

    profiles = capacity()
    lines += _family(
        "cass_profile_running",
        "gauge",
        "Runs active on each execution profile.",
        [_sample("cass_profile_running", item.running, {"profile": item.profile}) for item in profiles],
    )
    lines += _family(
        "cass_profile_capacity",
        "gauge",
        "Runs each execution profile admits at once (section 11).",
        [
            _sample("cass_profile_capacity", item.max_concurrent, {"profile": item.profile})
            for item in profiles
        ],
    )

    finished = (
        Run.objects.filter(state="succeeded", duration_seconds__isnull=False)
        .values("kind")
        .annotate(total=Count("id"), seconds=Sum("duration_seconds"))
    )
    duration_samples: list[str] = []
    for row in finished:
        duration_samples.append(
            _sample("cass_run_duration_seconds_sum", row["seconds"] or 0, {"kind": row["kind"]})
        )
        duration_samples.append(
            _sample("cass_run_duration_seconds_count", row["total"], {"kind": row["kind"]})
        )
    lines += _family(
        "cass_run_duration_seconds",
        "summary",
        "Wall-clock time of succeeded runs, by kind.",
        duration_samples,
    )

    recent = (
        Run.objects.filter(state="failed", finished_at__gte=moment - dt.timedelta(hours=24))
        .values("kind", "failure_stage")
        .annotate(total=Count("id"))
    )
    lines += _family(
        "cass_runs_failed_last_24h",
        "gauge",
        "Runs that failed in the last day, by kind and the stage they failed at.",
        [
            _sample(
                "cass_runs_failed_last_24h",
                row["total"],
                {"kind": row["kind"], "stage": row["failure_stage"] or "unknown"},
            )
            for row in recent
        ],
    )

    artifacts = Artifact.objects.values("state", "retention").annotate(
        total=Count("id"), size=Sum("size_bytes")
    )
    artifact_samples: list[str] = []
    byte_samples: list[str] = []
    for row in artifacts:
        labels = {"state": row["state"], "retention": row["retention"]}
        artifact_samples.append(_sample("cass_artifacts", row["total"], labels))
        byte_samples.append(_sample("cass_artifact_bytes", row["size"] or 0, labels))
    lines += _family(
        "cass_artifacts", "gauge", "Artifact records by state and retention class.", artifact_samples
    )
    lines += _family(
        "cass_artifact_bytes",
        "gauge",
        "Bytes recorded against artifacts by state and retention class.",
        byte_samples,
    )

    results = ResultSet.objects.values("state").annotate(total=Count("id"))
    lines += _family(
        "cass_results",
        "gauge",
        "Result sets by state; approved is the only one usable for decisions.",
        [_sample("cass_results", row["total"], {"state": row["state"]}) for row in results],
    )

    open_approvals = Approval.objects.filter(decision="requested").values("gate").annotate(
        total=Count("id")
    )
    lines += _family(
        "cass_approvals_open",
        "gauge",
        "Governance requests waiting for somebody to decide them.",
        [_sample("cass_approvals_open", row["total"], {"gate": row["gate"]}) for row in open_approvals],
    )

    return "\n".join(lines) + "\n"


class MetricsView(APIView):
    """The scrape endpoint.

    A scraper is not a person with a session, so it presents the token this
    installation configures. Without one configured, only a platform
    administrator may read the metrics: counts of runs and results are not
    business data, but they are not public either.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            200: OpenApiResponse(response=OpenApiTypes.STR, description="Prometheus text format."),
            403: OpenApiResponse(description="No scrape token and not an administrator."),
        }
    )
    def get(self, request, *args, **kwargs):
        token = getattr(settings, "CASS_METRICS_TOKEN", "")
        presented = request.META.get("HTTP_AUTHORIZATION", "")
        if token:
            allowed = presented == f"Bearer {token}"
        else:
            user = getattr(request, "user", None)
            allowed = bool(user and user.is_authenticated and user.is_platform_admin)
        if not allowed:
            return HttpResponse(
                "Metrics need the scrape token or a platform administrator.\n",
                status=403,
                content_type="text/plain; charset=utf-8",
            )
        return HttpResponse(collect(), content_type=CONTENT_TYPE)
