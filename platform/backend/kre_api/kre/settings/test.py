"""Test settings: SQLite and an eager task queue so tests need no services."""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR

DEBUG = False
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

KRE_ARTIFACT_BACKEND = "filesystem"
KRE_ARTIFACT_ROOT = str(BASE_DIR / ".test-artifacts")

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
