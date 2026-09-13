"""The data standards registry API.

Section 17 asks for the pinned OED version to be visible, the specification
readable, and two versions comparable. What this does not do is let a caller
change which version exposure is read against by posting a field: adoption is a
decision with a validation exercise behind it (section 8), so it is a
deliberate action by somebody who may publish models.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import MayPublishModels
from cass_oed.schema import OED_SCHEMA_VERSION

from . import registry, services
from .models import DataStandardVersion


class DataStandardVersionSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    matches_the_reader = serializers.SerializerMethodField()

    class Meta:
        model = DataStandardVersion
        fields = [
            "id", "standard", "version", "state", "source", "reference_uri",
            "checksum", "file_kinds", "field_count", "notes", "adopted_at",
            "is_active", "matches_the_reader", "created_at",
        ]
        read_only_fields = fields

    def get_matches_the_reader(self, obj) -> bool:
        """Whether this version is the one CASS actually reads exposure against.

        A registry saying one thing while the reader does another is worse than
        no registry, so the disagreement is reported rather than hidden.
        """
        return obj.version == OED_SCHEMA_VERSION


class DataStandardVersionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DataStandardVersion.objects.all()
    serializer_class = DataStandardVersionSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["standard", "state"]
    ordering_fields = ["version", "created_at"]

    @action(detail=True, methods=["get"])
    def fields(self, request, pk=None, version=None):
        """Every field this version of the standard defines, by file."""
        record = self.get_object()
        try:
            specification = services.specification_of(record)
        except (services.StandardRegistrationError, registry.StandardError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        wanted = request.query_params.get("file")
        return Response(
            {
                "standard": str(record),
                "files": {
                    name: [field.as_dict() for field in fields.values()]
                    for name, fields in specification.items()
                    if not wanted or name.lower() == wanted.lower()
                },
            }
        )

    @action(detail=True, methods=["get"])
    def coverage(self, request, pk=None, version=None):
        """What CASS reads of this version, and what it does not.

        A required field CASS does not read is a gap somebody chose; a column
        CASS reads that the standard does not define is a local invention. Both
        belong in front of whoever is deciding whether to adopt a version.
        """
        record = self.get_object()
        try:
            specification = services.specification_of(record)
        except (services.StandardRegistrationError, registry.StandardError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "standard": str(record),
                "reader_version": OED_SCHEMA_VERSION,
                "files": registry.coverage(specification),
            }
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("from", OpenApiTypes.STR, description="Version compared from."),
            OpenApiParameter("to", OpenApiTypes.STR, description="Version compared to."),
            OpenApiParameter("standard", OpenApiTypes.STR, description="Defaults to OED."),
        ]
    )
    @action(detail=False, methods=["get"])
    def diff(self, request, version=None):
        """What changed between two registered versions of one standard."""
        earlier_version = request.query_params.get("from")
        later_version = request.query_params.get("to")
        if not earlier_version or not later_version:
            return Response(
                {"detail": "Name both versions to compare, as from and to."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        standard = request.query_params.get("standard", "OED")
        records = {
            record.version: record
            for record in DataStandardVersion.objects.filter(
                standard=standard, version__in=[earlier_version, later_version]
            )
        }
        missing = [item for item in (earlier_version, later_version) if item not in records]
        if missing:
            return Response(
                {
                    "detail": (
                        f"{standard} {', '.join(missing)} is not registered, so there is "
                        "nothing to compare. Register the specification first."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            earlier = services.specification_of(records[earlier_version])
            later = services.specification_of(records[later_version])
        except (services.StandardRegistrationError, registry.StandardError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "standard": standard,
                "from": earlier_version,
                "to": later_version,
                "compared_at": timezone.now().isoformat(),
                **registry.compare(earlier, later),
            }
        )

    @action(detail=True, methods=["post"])
    def adopt(self, request, pk=None, version=None):
        """Make this the version exposure is read against.

        Section 8 requires the OED 5 decision to rest on a comparison against
        PiWind, the KRE extract and the pinned engine. This records that
        decision rather than taking it, and refuses a version the reader does
        not implement -- a registry that said one thing while the validator did
        another would be worse than no registry at all.
        """
        record = self.get_object()
        if record.version != OED_SCHEMA_VERSION:
            return Response(
                {
                    "detail": (
                        f"CASS reads exposure against OED {OED_SCHEMA_VERSION}. Adopting "
                        f"{record.version} in the registry alone would leave the registry "
                        "and the validator disagreeing. Change the reader in the same "
                        "release as the adoption."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        services.adopt(record, actor=request.user)
        audit.record(
            action=AuditAction.APPROVE,
            subject_type="data_standard_version",
            subject_id=record.id,
            actor=request.user,
            subject_label=str(record),
            after={"state": record.state},
            request=request,
        )
        return Response(self.get_serializer(record).data)
