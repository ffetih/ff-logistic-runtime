# Loads the files in assets/ when Superset starts: the analytics connection, the
# monthly dataset, its charts and the dashboard. A later start overwrites the same
# UUIDs. A chart edited in Superset and not exported is undone at the next start.
#
# The reader's password comes from ANALYTICS_PASSWORD. The embedded id is pinned
# below. A guest token names that id (identity copies dashboardId into the token
# unchanged). Superset matches it to this embed, not to the dashboard slug.
import os
import sys
import uuid
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
DATABASE_FILE = "databases/ff_logistics_analytics.yaml"
DATABASE_UUID = "c4a1e0b2-6f38-4d15-9a70-2e8b5c1d4f63"
READER_IN_FILE = "analytics_reader"
GUEST_ROLE = "TenantEmbedViewer"

# What an embedded dashboard needs to draw its chart, and nothing that writes or explores.
GUEST_PERMISSIONS = [
    ("can_read", "CurrentUserRestApi"),
    ("can_read", "Dashboard"),
    ("can_read", "Chart"),
    ("can_read", "Dataset"),
    ("can_read", "EmbeddedDashboard"),
    ("can_dashboard", "Superset"),
    ("can_explore_json", "Superset"),
    ("can_read", "DashboardFilterStateRestApi"),
    ("can_write", "DashboardFilterStateRestApi"),
    ("can_read", "DashboardPermalinkRestApi"),
    ("can_time_range", "Api"),
]

# dashboard UUID -> embedded UUID (the id a guest token names)
EMBEDDED = {
    "e5d0a8c3-1f47-4b92-a6e0-3c8d2b5f7041": "d18e4b70-9a25-4c63-b0f1-7e2a6c5d8349",
}


def read_assets(reader):
    contents = {}
    for path in sorted(ASSETS.rglob("*.yaml")):
        name = path.relative_to(ASSETS).as_posix()
        text = path.read_text(encoding="utf-8")
        if name == DATABASE_FILE and reader != READER_IN_FILE:
            text = text.replace(f"//{READER_IN_FILE}:", f"//{reader}:")
        contents[name] = text
    return contents


def adopt_database(db, Database):
    """The first cut registered this database without the pinned uuid. Drop that row
    so the import creates one connection, not a second with the same name."""
    pinned = uuid.UUID(DATABASE_UUID)
    for database in db.session.query(Database).filter_by(database_name="ff_logistics_analytics"):
        if database.uuid != pinned:
            db.session.delete(database)
            print(f"provision: removed the unpinned {database.database_name} connection")
    db.session.commit()


def ensure_guest_role(db, security_manager):
    role = security_manager.add_role(GUEST_ROLE)
    wanted = []
    for permission, view in GUEST_PERMISSIONS:
        pv = security_manager.find_permission_view_menu(permission, view)
        if pv is None:
            pv = security_manager.add_permission_view_menu(permission, view)
            print(f"provision: created permission {permission} on {view}")
        wanted.append(pv)
    role.permissions = wanted
    db.session.commit()
    print(f"provision: role {GUEST_ROLE} with {len(wanted)} permissions")


def pin_embedded(db, Dashboard, EmbeddedDashboard):
    for dashboard_uuid, embedded_uuid in EMBEDDED.items():
        dashboard = db.session.query(Dashboard).filter_by(uuid=uuid.UUID(dashboard_uuid)).one()
        dashboard.roles = []
        embedded = db.session.query(EmbeddedDashboard).filter_by(dashboard_id=dashboard.id).one_or_none()
        if embedded is None:
            embedded = EmbeddedDashboard(dashboard_id=dashboard.id)
            db.session.add(embedded)
        embedded.uuid = uuid.UUID(embedded_uuid)
        # The console (8090) and Vite's dev server (5173). The iframe sends that origin.
        embedded.allow_domain_list = "http://localhost:8090,http://localhost:5173"
        print(f"provision: '{dashboard.dashboard_title}' embedded as {embedded_uuid}")
    db.session.commit()


def main():
    password = os.environ.get("ANALYTICS_PASSWORD", "")
    if not password:
        print("provision: ANALYTICS_PASSWORD is not set", file=sys.stderr)
        return 1
    reader = os.environ.get("ANALYTICS_USER", READER_IN_FILE)
    contents = read_assets(reader)

    # The server process reads this name from superset_config. Set it here too, before
    # Superset imports that module, so this process agrees with the file.
    import superset_config

    superset_config.GUEST_ROLE_NAME = GUEST_ROLE

    from superset.app import create_app

    app = create_app()
    with app.app_context():
        from superset import db, security_manager
        from superset.commands.importers.v1.assets import ImportAssetsCommand
        from superset.models.core import Database
        from superset.models.dashboard import Dashboard
        from superset.models.embedded_dashboard import EmbeddedDashboard
        from superset.utils.core import override_user

        adopt_database(db, Database)
        admin = security_manager.find_user(username="admin")
        with override_user(admin):
            ImportAssetsCommand(contents, passwords={DATABASE_FILE: password}).run()
        print(f"provision: imported {len(contents) - 1} files from {ASSETS}")
        ensure_guest_role(db, security_manager)
        pin_embedded(db, Dashboard, EmbeddedDashboard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
