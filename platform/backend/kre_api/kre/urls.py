"""The KRE API surface.

Section 4 sets the boundary rule this file enforces: the React application
calls only the KRE API and never an engine API directly. Everything an analyst
needs is therefore routed here.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from apps.accounts.api import HealthView, PlatformInfoView, SessionView, UserViewSet
from apps.artifacts.api import ArtifactViewSet
from apps.audit.api import ApprovalViewSet, AuditEventViewSet
from apps.exposure.api import EnrichmentRunViewSet, ExposureVersionViewSet
from apps.modelregistry.api import (
    AreaPerilGridViewSet,
    AssumptionSetViewSet,
    ModelVersionViewSet,
    VulnerabilitySetViewSet,
)
from apps.projects.api import ProjectViewSet
from apps.results.api import ResultComparisonViewSet, ResultSetViewSet
from apps.runs.api import (
    AnalysisRunViewSet,
    ConversionRunViewSet,
    HazardRunViewSet,
    RunViewSet,
)

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("users", UserViewSet, basename="user")
router.register("artifacts", ArtifactViewSet, basename="artifact")
router.register("audit-events", AuditEventViewSet, basename="audit-event")
router.register("approvals", ApprovalViewSet, basename="approval")

router.register("exposure-versions", ExposureVersionViewSet, basename="exposure-version")
router.register("enrichment-runs", EnrichmentRunViewSet, basename="enrichment-run")

router.register("grids", AreaPerilGridViewSet, basename="grid")
router.register("vulnerability-sets", VulnerabilitySetViewSet, basename="vulnerability-set")
router.register("assumption-sets", AssumptionSetViewSet, basename="assumption-set")
router.register("model-versions", ModelVersionViewSet, basename="model-version")

router.register("runs", RunViewSet, basename="run")
router.register("hazard-runs", HazardRunViewSet, basename="hazard-run")
router.register("conversion-runs", ConversionRunViewSet, basename="conversion-run")
router.register("analysis-runs", AnalysisRunViewSet, basename="analysis-run")

router.register("results", ResultSetViewSet, basename="result")
router.register("comparisons", ResultComparisonViewSet, basename="comparison")

api_patterns = [
    path("session/", SessionView.as_view(), name="session"),
    path("platform/", PlatformInfoView.as_view(), name="platform-info"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthView.as_view(), name="health"),
    path("api/<version>/", include((api_patterns, "api"), namespace="api")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
