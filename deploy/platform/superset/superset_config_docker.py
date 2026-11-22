# Overrides the image's own config, which expects Redis. This cluster has none.
# The metadata database comes from DATABASE_* in the environment. The analytics
# database is registered separately, as analytics_reader.
#
# That role sees a row only after SET app.tenant_id. The policy does not read a
# WHERE, and set_config is revoked, so the chart's own statement cannot name the
# tenant. The connection does, from the guest token's tenant_id claim, before
# the chart SQL. No claim, or a claim that is not a tenant id, leaves the
# setting unset and the reader sees no rows. A signed-in admin is the same.
#
# The claim sits next to user and resources. rls_rules stays empty. Identity
# signs the same shape (S08-03): HS256, audience ff-logistic, this secret.
import os
import re

_TENANT_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_ANALYTICS_DB = "ff_logistics_analytics"

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# The host port is plain HTTP.
TALISMAN_ENABLED = False
SESSION_COOKIE_SECURE = False

FEATURE_FLAGS = {
    "ALERT_REPORTS": False,
    # Without this, a guest token never becomes the request user, so the
    # connection below would never see tenant_id.
    "EMBEDDED_SUPERSET": True,
}

GUEST_TOKEN_JWT_SECRET = os.environ["GUEST_TOKEN_JWT_SECRET"]
GUEST_TOKEN_JWT_AUDIENCE = "ff-logistic"

CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
# Chart results are not cached. Two guest tokens carry the same empty rls_rules,
# so Superset would reuse one tenant's result for the other.
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
FILTER_STATE_CACHE_CONFIG = CACHE_CONFIG
EXPLORE_FORM_DATA_CACHE_CONFIG = CACHE_CONFIG


class CeleryConfig:
    broker_url = "memory://"
    result_backend = None


CELERY_CONFIG = CeleryConfig


def _guest_tenant():
    from flask import g, has_request_context

    if not has_request_context():
        return None
    user = getattr(g, "user", None)
    if not getattr(user, "is_guest_user", False):
        return None
    token = getattr(user, "guest_token", None)
    tenant = token.get("tenant_id") if isinstance(token, dict) else None
    if isinstance(tenant, str) and _TENANT_ID.fullmatch(tenant):
        return tenant
    return None


def _connection_mutator(uri, params, username, security_manager, source):
    # libpq applies -c at connection start, as its own statement, before the
    # chart SQL. Each query opens a new connection (NullPool), so a tenant
    # cannot linger on a connection the next request reuses.
    if getattr(uri, "database", None) != _ANALYTICS_DB:
        return uri, params
    connect_args = dict(params.get("connect_args") or {})
    tenant = _guest_tenant()
    if tenant is None:
        if str(connect_args.get("options", "")).startswith("-c app.tenant_id="):
            connect_args.pop("options", None)
    else:
        connect_args["options"] = f"-c app.tenant_id={tenant}"
    if connect_args:
        params["connect_args"] = connect_args
    else:
        params.pop("connect_args", None)
    return uri, params


DB_CONNECTION_MUTATOR = _connection_mutator
