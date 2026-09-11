"""Base settings for the KRE control plane.

Section 4 of the build plan gives Django a specific job: identity,
orchestration, metadata, lineage and audit. It holds references to large
artifacts, never the scientific arrays themselves. These settings are arranged
so that separation stays visible -- there is a database, and separately there
is an artifact store, and the two are configured independently.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# -- core -------------------------------------------------------------------

SECRET_KEY = env("KRE_SECRET_KEY", "insecure-development-key-replace-in-every-deployment")
DEBUG = env_bool("KRE_DEBUG", False)
ALLOWED_HOSTS = env_list("KRE_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "apps.accounts",
    "apps.projects",
    "apps.artifacts",
    "apps.audit",
    "apps.modelregistry",
    "apps.exposure",
    "apps.runs",
    "apps.results",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Attaches a correlation ID to every request so a run can be followed
    # across Django, the workers and the engine adapters (section 4).
    "apps.audit.middleware.CorrelationIDMiddleware",
]

ROOT_URLCONF = "kre.urls"
WSGI_APPLICATION = "kre.wsgi.application"
ASGI_APPLICATION = "kre.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_USER_MODEL = "accounts.User"

# -- database ---------------------------------------------------------------
# PostgreSQL holds control-plane records only. Section 5 is explicit that there
# is no event-site-IMT observation table here.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("KRE_DB_NAME", "kre"),
        "USER": env("KRE_DB_USER", "kre"),
        "PASSWORD": env("KRE_DB_PASSWORD", "kre"),
        "HOST": env("KRE_DB_HOST", "localhost"),
        "PORT": env("KRE_DB_PORT", "5432"),
        "CONN_MAX_AGE": int(env("KRE_DB_CONN_MAX_AGE", "60")),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -- artifact store ---------------------------------------------------------
# Configured separately from the database on purpose: moving from a local
# filesystem store to S3 must not touch application code.

KRE_ARTIFACT_BACKEND = env("KRE_ARTIFACT_BACKEND", "filesystem")
KRE_ARTIFACT_ROOT = env("KRE_ARTIFACT_ROOT", str(BASE_DIR.parent.parent / ".artifacts"))
KRE_S3_ENDPOINT = env("KRE_S3_ENDPOINT")
KRE_S3_REGION = env("KRE_S3_REGION", "us-east-1")

#: Buckets by purpose, so a retention or access rule can be applied to a whole
#: class of objects rather than object by object.
KRE_BUCKETS = {
    "upload": env("KRE_BUCKET_UPLOAD", "kre-upload"),
    "portfolio": env("KRE_BUCKET_PORTFOLIO", "kre-portfolio"),
    "model": env("KRE_BUCKET_MODEL", "kre-model"),
    "hazard": env("KRE_BUCKET_HAZARD", "kre-hazard"),
    "result": env("KRE_BUCKET_RESULT", "kre-result"),
}

#: Cross-service fixtures, including the official PiWind exposure. The
#: repository layout and the container layout differ, so the location is a
#: setting rather than a walk up from __file__.
KRE_FIXTURE_ROOT = env("KRE_FIXTURE_ROOT", str(BASE_DIR.parent / "tests" / "fixtures"))

#: Largest upload the API will register. Larger scientific artifacts are
#: written by workers directly into the store, never through Django.
KRE_MAX_UPLOAD_BYTES = int(env("KRE_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))

# -- background execution ---------------------------------------------------

CELERY_BROKER_URL = env("KRE_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("KRE_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TIMEZONE = "UTC"

#: Execution profiles from section 11. Concurrency and memory are declared
#: rather than inherited from maximum parallelism, so one large job cannot
#: exhaust the host.
KRE_EXECUTION_PROFILES = {
    "small": {"cpu": 2, "memory_gb": 4, "timeout_seconds": 1800, "max_concurrent": 4},
    "standard": {"cpu": 4, "memory_gb": 16, "timeout_seconds": 7200, "max_concurrent": 2},
    "large": {"cpu": 8, "memory_gb": 48, "timeout_seconds": 28800, "max_concurrent": 1},
    "model_build": {"cpu": 8, "memory_gb": 64, "timeout_seconds": 86400, "max_concurrent": 1},
}
KRE_DEFAULT_EXECUTION_PROFILE = env("KRE_DEFAULT_EXECUTION_PROFILE", "standard")

# -- engine adapters --------------------------------------------------------

KRE_OPENQUAKE_URL = env("KRE_OPENQUAKE_URL", "http://openquake:8800")
KRE_OASIS_API_URL = env("KRE_OASIS_API_URL", "http://oasis-api:8000")
KRE_KEYS_SERVICE_URL = env("KRE_KEYS_SERVICE_URL", "http://keys:8010")
KRE_CONVERTER_URL = env("KRE_CONVERTER_URL", "http://converter:8020")

#: Tested engine combinations. Section 18 requires promotion only after
#: contract and regression suites pass, so this is data a release updates.
KRE_COMPATIBILITY_MATRIX = [
    {"oasis": "2.5.7", "oed": "4.0.0", "ods_tools": "4.3.5"},
]

# -- API --------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "KRE Catastrophe Modelling Platform API",
    "DESCRIPTION": (
        "The KRE control-plane API. The React application calls only this API; "
        "it never calls OpenQuake or Oasis directly."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

CORS_ALLOWED_ORIGINS = env_list("KRE_CORS_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = True

# -- security ---------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env_list("KRE_CSRF_TRUSTED_ORIGINS", "http://localhost:5173")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# -- localisation and static ------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# -- observability ----------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "apps.audit.logging.StructuredFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
    },
    "root": {"handlers": ["console"], "level": env("KRE_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "kre": {"level": env("KRE_LOG_LEVEL", "INFO"), "handlers": ["console"], "propagate": False},
    },
}
