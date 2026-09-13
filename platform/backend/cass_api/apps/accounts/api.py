"""Identity endpoints and the platform health view."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import models
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
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
            "platform_role", "job_title", "local_install_approved", "is_active",
            "capabilities",
        ]
        # Changed through the administration serializer, never through this one.
        read_only_fields = ["id", "full_name", "is_active", "capabilities"]

    def get_capabilities(self, obj) -> dict:
        """What the interface should offer this person.

        Returned as data rather than inferred in the browser, so the React
        application never has to reimplement a permission rule.
        """
        return {
            "publish_models": obj.may_publish_models,
            "approve_gates": obj.may_approve_gates,
            "administer_platform": obj.is_platform_admin,
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


class UserAdministrationSerializer(serializers.ModelSerializer):
    """What an administrator may change about a person, and nothing else.

    Not the username, the password or the name: identity is the identity
    provider's, and a platform screen that edited it would make two sources of
    truth for who somebody is. What is the platform's to decide is what they
    may do here.
    """

    class Meta:
        model = User
        fields = ["platform_role", "job_title", "local_install_approved", "is_active"]


class UserViewSet(mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    """User directory, used when granting project membership.

    Everyone signed in may read it. Only a platform administrator may change a
    person's role, job title, local-install approval or whether they are active
    -- and never in a way that leaves the installation with nobody who can.
    """

    queryset = User.objects.filter(is_active=True).order_by("username")
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["platform_role"]
    search_fields = ["username", "first_name", "last_name", "email"]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        """Administrators see inactive people too, or nobody could reactivate one."""
        user = self.request.user
        if getattr(user, "is_platform_admin", False):
            return User.objects.order_by("username")
        return super().get_queryset()

    def get_serializer_class(self):
        if self.action == "partial_update":
            return UserAdministrationSerializer
        return super().get_serializer_class()

    def partial_update(self, request, *args, **kwargs):
        """Change what somebody may do, refusing the changes that lock everyone out.

        Two refusals, both about the same failure. An administrator may not
        remove their own administration or deactivate themselves: the next
        request would be from somebody who can no longer undo it. And no change
        may leave the installation with no active administrator at all, which
        is the same lock-out reached by changing somebody else.
        """
        if not request.user.is_platform_admin:
            return Response(
                {"detail": "Changing a person's role requires a platform administrator."},
                status=status.HTTP_403_FORBIDDEN,
            )

        person = self.get_object()
        serializer = UserAdministrationSerializer(person, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = serializer.validated_data

        keeps_admin = (
            changes.get("platform_role", person.platform_role) == "admin"
            or person.is_superuser
        ) and changes.get("is_active", person.is_active)

        if person.pk == request.user.pk and not keeps_admin:
            return Response(
                {
                    "detail": (
                        "You may not remove your own administration or deactivate "
                        "yourself. Ask another administrator, so the change can be "
                        "undone by somebody who still can."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if person.is_platform_admin and not keeps_admin:
            others = (
                User.objects.filter(is_active=True)
                .exclude(pk=person.pk)
                .filter(models.Q(platform_role="admin") | models.Q(is_superuser=True))
                .exists()
            )
            if not others:
                return Response(
                    {
                        "detail": (
                            "This would leave the installation with no active "
                            "administrator, and nobody able to reverse it."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        before = {field: getattr(person, field) for field in changes}
        serializer.save()
        audit.record(
            action=AuditAction.UPDATE,
            subject_type="user",
            subject_id=person.id,
            actor=request.user,
            subject_label=str(person),
            before=before,
            after={field: getattr(person, field) for field in changes},
            request=request,
        )
        return Response(UserSerializer(person).data)

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
        from apps.runs.admission import capacity
        from cass_oed.schema import OED_SCHEMA_VERSION

        return Response(
            {
                "api_version": "1.0.0",
                "oed_schema_version": OED_SCHEMA_VERSION,
                "compatibility_matrix": settings.CASS_COMPATIBILITY_MATRIX,
                "execution_profiles": settings.CASS_EXECUTION_PROFILES,
                # What each profile declares is only half of it; section 11
                # asks for admission control, so what is using it now travels
                # with it and the builder can say "full" before somebody waits.
                "profile_capacity": [item.as_dict() for item in capacity()],
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
