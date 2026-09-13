"""The support bundle: what an operator sends when something is wrong.

Section 11 requires an exportable support bundle carrying versions and health,
and is specific about what it must not carry: portfolio contents. The engine
adapters have long written their failure detail "for the support bundle", and
there was no bundle.

Two choices shape it, and both are about what leaves rather than what is
convenient to include.

Settings are included by allowlist. The platform holds five secrets today --
the Django key, the database password, two engine passwords and the metrics
scrape token -- and a denylist of their names would include the sixth one
somebody adds next quarter. An allowlist fails the other way: a new setting is
missing from the bundle until somebody decides it belongs there. Where the
interesting fact about a secret is whether it is set, that is what is reported.

Failures are reported by where they happened, not by what they said. A run's
failure summary is written for an analyst and names the portfolio -- how much
insured value could not be mapped, which locations -- so the bundle carries the
kind, the stage, the time and the correlation ID, which is enough to find the
full story in the logs of an installation that is entitled to read it.
"""

from __future__ import annotations

import datetime as dt
import platform as runtime
from importlib import metadata
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Count, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditAction

#: The settings a bundle may carry. Adding one is a decision that it is safe to
#: send outside the installation.
ALLOWED_SETTINGS: tuple[str, ...] = (
    "DEBUG",
    "TIME_ZONE",
    "CASS_ARTIFACT_BACKEND",
    "CASS_BUCKETS",
    "CASS_DEFAULT_EXECUTION_PROFILE",
    "CASS_EXECUTION_PROFILES",
    "CASS_MAX_GRID_CELLS",
    "CASS_MAX_UPLOAD_BYTES",
    "CASS_UPLOAD_SESSION_SECONDS",
    "CASS_OPENQUAKE_URL",
    "CASS_OASIS_API_URL",
    "CASS_KEYS_SERVICE_URL",
    "CASS_CONVERTER_URL",
    "CASS_OASIS_MODEL_SUPPLIER_ID",
    "CASS_OASIS_MODEL_ID",
    "CASS_OASIS_MODEL_VERSION_ID",
    "CELERY_TIMEZONE",
)

#: Settings whose only safe fact is whether they are set.
PRESENCE_ONLY: tuple[str, ...] = (
    "CASS_CLAMD_HOST",
    "CASS_METRICS_TOKEN",
    "CASS_OPENQUAKE_PASSWORD",
    "CASS_OASIS_PASSWORD",
)

#: Packages whose versions decide behaviour an operator may be asked about.
PACKAGES: tuple[str, ...] = (
    "django",
    "djangorestframework",
    "celery",
    "boto3",
    "h5py",
    "numpy",
    "cass-core",
    "cass-oed",
    "cass-converter",
    "cass-keys",
)

#: How many recent failures travel. Enough to see a pattern, few enough to read.
RECENT_FAILURES = 50


def _package_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = "not installed"
    return found


def _migrations() -> dict[str, str]:
    """The latest applied migration per app, which is what a schema question needs."""
    from django.db.migrations.recorder import MigrationRecorder

    latest: dict[str, str] = {}
    for app, name in MigrationRecorder(connection).migration_qs.values_list("app", "name"):
        if name > latest.get(app, ""):
            latest[app] = name
    return dict(sorted(latest.items()))


def bundle(*, now: dt.datetime | None = None, probe_engines: bool = True) -> dict[str, Any]:
    """Everything an operator may send, and nothing from inside a portfolio.

    ``probe_engines`` exists so a caller that cannot reach the engines -- a test,
    or an installation whose engines are the problem -- still gets a bundle, with
    the probe recorded as skipped rather than the bundle failing.
    """
    from apps.artifacts import retention
    from apps.artifacts.models import Artifact
    from apps.audit.models import Approval
    from apps.results.models import ResultSet
    from apps.runs.admission import capacity
    from apps.runs.models import Run
    from cass_oed.schema import OED_SCHEMA_VERSION

    moment = now or timezone.now()

    engines: dict[str, Any]
    if probe_engines:
        from apps.common.engines import describe_engines

        try:
            engines = describe_engines()
        except Exception as exc:  # noqa: BLE001 - a bundle must survive a broken engine
            engines = {"probe_failed": str(exc)[:500]}
    else:
        engines = {"probe_skipped": True}

    failures = (
        Run.objects.filter(state="failed")
        .order_by("-finished_at", "-created_at")
        .values("id", "kind", "failure_stage", "execution_profile", "correlation_id", "finished_at")[
            :RECENT_FAILURES
        ]
    )

    sweep = retention.sweep(now=moment, dry_run=True)

    return {
        "generated_at": moment.isoformat(),
        "installation": {
            "api_version": "1.0.0",
            "oed_schema_version": OED_SCHEMA_VERSION,
            "compatibility_matrix": getattr(settings, "CASS_COMPATIBILITY_MATRIX", []),
            "python": runtime.python_version(),
            "packages": _package_versions(),
            "migrations": _migrations(),
        },
        "settings": {
            name: getattr(settings, name) for name in ALLOWED_SETTINGS if hasattr(settings, name)
        },
        "configured": {name: bool(getattr(settings, name, "")) for name in PRESENCE_ONLY},
        "engines": engines,
        "capacity": [item.as_dict() for item in capacity()],
        "runs": [
            {"kind": row["kind"], "state": row["state"], "count": row["total"]}
            for row in Run.objects.values("kind", "state").annotate(total=Count("id"))
        ],
        "recent_failures": [
            {
                "run": str(row["id"]),
                "kind": row["kind"],
                "stage": row["failure_stage"],
                "profile": row["execution_profile"],
                "correlation_id": row["correlation_id"],
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            }
            for row in failures
        ],
        "artifacts": [
            {
                "state": row["state"],
                "retention": row["retention"],
                "count": row["total"],
                "bytes": row["size"] or 0,
            }
            for row in Artifact.objects.values("state", "retention").annotate(
                total=Count("id"), size=Sum("size_bytes")
            )
        ],
        "retention": {
            "due_now": sweep["expired_count"],
            "kept_as_evidence": sweep["kept_count"],
            "abandoned_uploads": sweep["abandoned_upload_count"],
        },
        "results": [
            {"state": row["state"], "count": row["total"]}
            for row in ResultSet.objects.values("state").annotate(total=Count("id"))
        ],
        "open_approvals": Approval.objects.filter(decision="requested").count(),
    }


class SupportBundleView(APIView):
    """The support bundle, for a platform administrator.

    An administrator rather than anyone signed in: the bundle is written to
    leave the installation, and deciding what leaves is an administrator's act.

    Looking at it and taking it away are audited as different acts. The
    administration screen reads the bundle to show queues, storage and
    retention; recording every visit to that screen as a download would bury
    the one download that matters under a hundred that did not happen. So
    ``?download=1`` is the download -- served as a file and audited as one --
    and anything else is a read.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "download",
                OpenApiTypes.BOOL,
                description="Serve as a file and audit as a download rather than a read.",
            )
        ],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Versions, health, capacity and counts; no secrets, no portfolio contents.",
            ),
            403: OpenApiResponse(description="Not a platform administrator."),
        }
    )
    def get(self, request, *args, **kwargs):
        if not request.user.is_platform_admin:
            return Response(
                {"detail": "The support bundle is for a platform administrator."},
                status=status.HTTP_403_FORBIDDEN,
            )

        download = str(request.query_params.get("download", "")).lower() in ("1", "true", "yes")
        carried = bundle()
        audit.record(
            action=AuditAction.DOWNLOAD if download else AuditAction.READ,
            subject_type="support_bundle",
            subject_id="installation",
            actor=request.user,
            subject_label="support bundle",
            request=request,
        )
        response = Response(carried)
        if download:
            stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
            response["Content-Disposition"] = (
                f'attachment; filename="cass-support-bundle-{stamp}.json"'
            )
        return response
