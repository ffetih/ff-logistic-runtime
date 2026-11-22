# One database connection, to the loader's database, as the reader role.
# Idempotent: a later start updates the same row.
import os

from superset.app import create_app

NAME = "ff_logistics_analytics"

app = create_app()
with app.app_context():
    from superset import db
    from superset.models.core import Database

    uri = os.environ["ANALYTICS_SQLALCHEMY_URI"]
    database = db.session.query(Database).filter_by(database_name=NAME).one_or_none()
    if database is None:
        database = Database(database_name=NAME)
        db.session.add(database)
    database.set_sqlalchemy_uri(uri)
    database.expose_in_sqllab = True
    db.session.commit()
    print(f"database {NAME} is there")
