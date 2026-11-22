# Overrides the image's own config, which expects Redis. This cluster has none.
# The metadata database comes from DATABASE_* in the environment. The analytics
# database is registered separately, as analytics_reader. Its session is not
# given a tenant here: that is the next item, and until then the reader sees
# no rows.
import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# The host port is plain HTTP.
TALISMAN_ENABLED = False
SESSION_COOKIE_SECURE = False

FEATURE_FLAGS = {"ALERT_REPORTS": False}

CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
DATA_CACHE_CONFIG = CACHE_CONFIG
FILTER_STATE_CACHE_CONFIG = CACHE_CONFIG
EXPLORE_FORM_DATA_CACHE_CONFIG = CACHE_CONFIG


class CeleryConfig:
    broker_url = "memory://"
    result_backend = None


CELERY_CONFIG = CeleryConfig
