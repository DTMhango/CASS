"""Identity endpoints and the platform health view."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditAction

from .models import User


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "full_name",
            "platform_role", "job_title", "local_install_approved", "capabilities",
        ]
        read_only_fields = ["id", "full_name", "capabilities"]

    def get_capabilities(self, obj) -> dict:
        """What the interface should offer this person.

        Returned as data rather than inferred in the browser, so the React
        application never has to reimplement a permission rule.
        """
        return {
            "publish_models": obj.may_publish_models,
            "approve_gates": obj.may_approve_gates,
            "administer_platform": obj.is_platform_admin,
            "see_counterparty_names": obj.may_see_counterparty_names,
        }


class SignInSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class SessionSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    user = UserSerializer(required=False)


class SessionView(APIView):
    """Sign in, sign out and identify the current user."""

    permission_classes = [AllowAny]
    serializer_class = SessionSerializer

    @extend_schema(responses=SessionSerializer)
    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response({"authenticated": False}, status=status.HTTP_200_OK)
        return Response(
            {"authenticated": True, "user": UserSerializer(request.user).data}
        )

    @extend_schema(request=SignInSerializer, responses=SessionSerializer)
    def post(self, request, *args, **kwargs):
        username = request.data.get("username", "")
        password = request.data.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is None:
            # Failed attempts are audited; section 10 requires access records.
            audit.record(
                action=AuditAction.SIGN_IN_FAILED,
                subject_type="user",
                subject_id=username or "unknown",
                subject_label=username,
                request=request,
            )
            return Response(
                {"detail": "Those sign-in details were not recognised."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        audit.record(
            action=AuditAction.SIGN_IN,
            subject_type="user",
            subject_id=user.id,
            actor=user,
            subject_label=str(user),
            request=request,
        )
        return Response({"authenticated": True, "user": UserSerializer(user).data})

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, *args, **kwargs):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """User directory, used when granting project membership."""

    queryset = User.objects.filter(is_active=True).order_by("username")
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["platform_role"]
    search_fields = ["username", "first_name", "last_name", "email"]

    @extend_schema(responses=UserSerializer)
    @action(detail=False, methods=["get"])
    def me(self, request, version=None):
        return Response(UserSerializer(request.user).data)


class PlatformInfoView(APIView):
    """Versions, profiles and engine endpoints.

    Section 4 requires local installations to show whether they are approved
    and unmodified, and section 11 requires an exportable support bundle
    containing versions and health without portfolio contents. This is the
    metadata half of that.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=inline_serializer(
            name="PlatformInfo",
            fields={
                "api_version": serializers.CharField(),
                "oed_schema_version": serializers.CharField(),
                "compatibility_matrix": serializers.ListField(),
                "execution_profiles": serializers.DictField(),
                "default_execution_profile": serializers.CharField(),
                "artifact_backend": serializers.CharField(),
                "engines": serializers.DictField(),
            },
        )
    )
    def get(self, request, *args, **kwargs):
        from cass_oed.schema import OED_SCHEMA_VERSION

        return Response(
            {
                "api_version": "1.0.0",
                "oed_schema_version": OED_SCHEMA_VERSION,
                "compatibility_matrix": settings.CASS_COMPATIBILITY_MATRIX,
                "execution_profiles": settings.CASS_EXECUTION_PROFILES,
                "default_execution_profile": settings.CASS_DEFAULT_EXECUTION_PROFILE,
                "artifact_backend": settings.CASS_ARTIFACT_BACKEND,
                "engines": {
                    "openquake": settings.CASS_OPENQUAKE_URL,
                    "oasis": settings.CASS_OASIS_API_URL,
                    "keys": settings.CASS_KEYS_SERVICE_URL,
                    "converter": settings.CASS_CONVERTER_URL,
                },
            }
        )


class EngineStatusView(APIView):
    """Whether each engine is reachable and running a tested version.

    Kept apart from the platform metadata view because this one talks to the
    engines. Metadata is a cheap read that a screen can poll; this is a network
    call per engine, and folding the two together would make every page load
    wait on an Oasis server that might be down.

    Section 18 is the reason the answer is not merely "reachable": an engine
    answering on an untested version is a compatibility question an operator
    has to see before submitting a run, not after one produces a result nobody
    can defend.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=inline_serializer(
            name="EngineStatus",
            fields={"engines": serializers.DictField()},
        )
    )
    def get(self, request, *args, **kwargs):
        from apps.common.engines import describe_engines

        return Response({"engines": describe_engines()})


class HealthView(APIView):
    """Liveness and readiness, unauthenticated so a probe can reach it."""

    permission_classes = [AllowAny]

    @extend_schema(
        responses=inline_serializer(
            name="Health",
            fields={
                "status": serializers.CharField(),
                "checks": serializers.DictField(child=serializers.CharField()),
            },
        )
    )
    def get(self, request, *args, **kwargs):
        from django.db import connection

        checks = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:  # pragma: no cover - depends on deployment
            checks["database"] = f"error: {exc}"

        try:
            from apps.common.storage import get_store

            get_store()
            checks["artifact_store"] = "ok"
        except Exception as exc:  # pragma: no cover
            checks["artifact_store"] = f"error: {exc}"

        healthy = all(value == "ok" for value in checks.values())
        return Response(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
