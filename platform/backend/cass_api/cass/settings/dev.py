"""Development settings: Docker Compose on a workstation."""

from .base import *  # noqa: F401,F403
from .base import env_bool

DEBUG = env_bool("CASS_DEBUG", True)
ALLOWED_HOSTS = ["*"]
