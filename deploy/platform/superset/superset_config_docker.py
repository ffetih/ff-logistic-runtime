# Overrides the image's own config, which expects Redis. This cluster has none.
# The metadata database comes from DATABASE_* in the environment. The analytics
# database is registered separately, as analytics_reader.
#
# That role sees a row only after SET app.tenant_id. The policy does not read a
# WHERE, and set_config is revoked, so the chart's own statement cannot name the
# tenant. The connection does, from the guest token's app.tenant_id claim, before
# the chart SQL. No claim, or a claim that is not a tenant id, leaves the
# setting unset and the reader sees no rows. A signed-in admin is the same.
#
# Identity signs that claim (S08-03): HS256, audience ff-logistic, this secret.
# The token also carries an rls_rules clause. The policy does not read it.
# A chart statement that would SET or RESET the scope is refused here. Superset
# runs that statement on a raw cursor, so the refusal is this hook, not an event.
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
    app_claim = token.get("app") if isinstance(token, dict) else None
    tenant = app_claim.get("tenant_id") if isinstance(app_claim, dict) else None
    if isinstance(tenant, str) and _TENANT_ID.fullmatch(tenant):
        return tenant
    return None


_SQL_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_UNICODE_ESCAPE = re.compile(r"\bu&\s*['\"]|\buescape\b", re.IGNORECASE)
_SQL_STRING = re.compile(r"'(?:[^']|'')*'|\$(\w*)\$.*?\$\1\$", re.DOTALL)
_SCOPE_CHANGE = re.compile(
    r"\bset_config\b"
    r"|\breset\s+all\b"
    r"|\breset\s+(session\s+)?app\s*\.\s*(tenant_id|all_tenants)\b"
    r"|\bset\s+(session\s+|local\s+)?app\s*\.\s*(tenant_id|all_tenants)\b"
    r"|\b(query_to_xml|query_to_xmlschema|query_to_xml_and_xmlschema|cursor_to_xml|dblink\w*)\b"
    r"|(^|;)\s*do\b"
    r"|;\s*\S",
    re.IGNORECASE,
)


class AnalyticsScopeChangeForbidden(Exception):
    """A query could change app.tenant_id or app.all_tenants."""


def _changes_analytics_scope(statement):
    if not isinstance(statement, str):
        return False
    code = _SQL_COMMENT.sub(" ", statement)
    if _UNICODE_ESCAPE.search(code):
        return True
    code = _SQL_STRING.sub(" ", code).replace('"', "").strip().rstrip(";")
    return _SCOPE_CHANGE.search(code) is not None


def _is_analytics(database):
    url = getattr(database, "url_object", None)
    return getattr(url, "database", None) == _ANALYTICS_DB


def SQL_QUERY_MUTATOR(sql, **kwargs):
    """Superset calls this with the final SQL of every chart and SQL Lab query."""
    if _is_analytics(kwargs.get("database")) and _changes_analytics_scope(sql):
        raise AnalyticsScopeChangeForbidden(
            "A query cannot change app.tenant_id or app.all_tenants"
        )
    return sql


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
