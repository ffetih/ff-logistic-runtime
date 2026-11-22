# The image loads a module named superset_config and nothing else. Its own default
# metadata database is sqlite, so the Postgres URI is set here, from DATABASE_*.
# This cluster has no Redis. The analytics database is registered separately,
# as analytics_reader.
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
from urllib.parse import quote_plus

_TENANT_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_ANALYTICS_DB = "ff_logistics_analytics"

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Overrides the image default, which is a sqlite file inside the container.
# The init container and the server do not share that file.
SQLALCHEMY_DATABASE_URI = (
    f"{os.environ['DATABASE_DIALECT']}://"
    f"{quote_plus(os.environ['DATABASE_USER'])}:{quote_plus(os.environ['DATABASE_PASSWORD'])}"
    f"@{os.environ['DATABASE_HOST']}:{os.environ['DATABASE_PORT']}/{os.environ['DATABASE_DB']}"
)

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
# provision.py creates this role. A guest token gets it, not Public.
GUEST_ROLE_NAME = "TenantEmbedViewer"

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
_DOLLAR_QUOTE = re.compile(r"\$(\w*)\$")


class AnalyticsScopeChangeForbidden(Exception):
    """A query could change app.tenant_id or app.all_tenants."""


def _changes_analytics_scope(statement):
    """True when the statement could change the tenant, or is more than one statement.

    Strings are masked before comments are. A `--` or `/*` inside a string is not a
    comment, so it must not hide the statement that follows it. `E'\\''` is one quote
    inside an escape string, and the semicolon after it is a second statement.
    """
    if not isinstance(statement, str):
        return False
    code = _sql_outside_literals(statement)
    if code is None:
        return True
    code = code.replace('"', "").strip().rstrip(";")
    return _SCOPE_CHANGE.search(code) is not None


def _sql_outside_literals(sql):
    """The statement with literals and comments replaced by a space.

    Returns None for a Unicode escape (`U&` or `UESCAPE`): those can spell a keyword
    without writing it.
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline < 0 else newline
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        if sql[i] in ("E", "e") and i + 1 < n and sql[i + 1] == "'":
            i = _skip_escape_string(sql, i + 2)
            out.append(" ")
            continue
        if sql[i] == "'":
            i = _skip_standard_string(sql, i + 1)
            out.append(" ")
            continue
        if sql[i] == "$":
            match = _DOLLAR_QUOTE.match(sql, i)
            if match:
                tag = match.group(0)
                end = sql.find(tag, i + len(tag))
                i = n if end < 0 else end + len(tag)
                out.append(" ")
                continue
        if sql.startswith(("U&", "u&"), i) or _at_keyword(sql, i, "uescape"):
            return None
        out.append(sql[i])
        i += 1
    return "".join(out)


def _at_keyword(sql, i, word):
    tail = sql[i:i + len(word) + 1]
    if not tail.lower().startswith(word):
        return False
    return len(tail) == len(word) or not (tail[-1].isalnum() or tail[-1] == "_")


def _skip_standard_string(sql, i):
    n = len(sql)
    while i < n:
        if sql[i] == "'":
            if i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _skip_escape_string(sql, i):
    n = len(sql)
    while i < n:
        if sql[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if sql[i] == "'":
            if i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return n


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
