"""Production settings: everything secret comes from the environment."""

from .base import *  # noqa: F401,F403
from .base import env, env_bool

DEBUG = False

SECURE_SSL_REDIRECT = env_bool("CASS_SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(env("CASS_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if SECRET_KEY.startswith("insecure-"):  # noqa: F405
    raise RuntimeError("CASS_SECRET_KEY must be set for a production deployment")
