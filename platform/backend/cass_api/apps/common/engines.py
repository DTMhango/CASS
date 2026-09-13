"""How the control plane reaches an engine.

Section 4 puts every engine behind a versioned adapter and forbids the React
application from calling an engine API directly. That makes this module the
single place where deployment configuration becomes a live adapter: nothing
else in the control plane should read an engine URL or a credential.

Adapters are built per call rather than cached. An adapter holds an access
token, and a token is per-session state rather than the stateless client the
artifact store keeps, so sharing one instance across requests and Celery tasks
would mean sharing a refresh cycle between them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings

from cass_adapters.oasis import OasisAdapter
from cass_adapters.openquake import OpenQuakeAdapter


def oasis_adapter(**overrides: Any) -> OasisAdapter:
    """Build an Oasis adapter from the deployment configuration."""
    kwargs: dict[str, Any] = {
        "username": settings.CASS_OASIS_USERNAME,
        "password": settings.CASS_OASIS_PASSWORD,
    }
    kwargs.update(overrides)
    return OasisAdapter(settings.CASS_OASIS_API_URL, **kwargs)


def openquake_adapter(**overrides: Any) -> OpenQuakeAdapter:
    """Build an OpenQuake adapter from the deployment configuration."""
    kwargs: dict[str, Any] = {
        "username": settings.CASS_OPENQUAKE_USERNAME,
        "password": settings.CASS_OPENQUAKE_PASSWORD,
    }
    kwargs.update(overrides)
    return OpenQuakeAdapter(settings.CASS_OPENQUAKE_URL, **kwargs)


def oasis_model_triple() -> tuple[str, str, str]:
    """The supplier, model and version identifiers the Oasis server registers.

    Section 12 requires a result to be traceable to an immutable model version.
    The triple is configuration rather than a stored numeric id so that a
    rebuilt Oasis server cannot silently point a CASS run at whichever model
    inherited the same primary key.
    """
    return (
        settings.CASS_OASIS_MODEL_SUPPLIER_ID,
        settings.CASS_OASIS_MODEL_ID,
        str(settings.CASS_OASIS_MODEL_VERSION_ID),
    )


def describe_engines() -> dict[str, Mapping[str, Any]]:
    """Report each engine's reachability and version compatibility.

    Section 4 requires a local installation to show whether it is approved and
    unmodified, and section 11 requires a support bundle carrying versions and
    health without portfolio contents. This is the live half of that; the
    static half is the platform metadata endpoint.

    Every engine the deployment runs gets a row, reachable or not. A missing
    row reads as "nothing to see", which is the wrong thing to tell an operator
    about a service that is down. The configured URL travels with each answer
    so the support bundle records what was probed, not merely what replied.
    """
    return {
        "oasis": {**oasis_adapter().describe(), "url": settings.CASS_OASIS_API_URL},
        "openquake": {
            **openquake_adapter().describe(),
            "url": settings.CASS_OPENQUAKE_URL,
        },
    }
