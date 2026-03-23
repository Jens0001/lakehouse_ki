"""
Airflow 3.x User-Initialisierung.

In Airflow 3.x gibt es keinen 'airflow users create' CLI-Befehl mehr.
Der FAB (Flask-AppBuilder) Security Manager wird direkt über Python
angesprochen, um den Admin-User programmatisch anzulegen.

Dieses Script wird beim Container-Start VOR dem API-Server ausgeführt
und stellt sicher, dass mindestens ein Admin-User existiert.
"""
from airflow.providers.fab.www.app import create_app
from airflow.providers.fab.auth_manager.security_manager.override import (
    FabAirflowSecurityManagerOverride,
)

# Flask-App ohne Plugins erstellen – nur für DB-Zugriff
app = create_app(enable_plugins=False)

with app.app_context():
    # FabAirflowSecurityManagerOverride ist der einzige SM in Airflow 3.x,
    # der add_user/find_user/find_role Methoden hat (AirflowSecurityManagerV2
    # hat diese Methoden NICHT)
    sm = FabAirflowSecurityManagerOverride(app.appbuilder)

    # Admin-User nur anlegen, wenn noch nicht vorhanden
    if not sm.find_user(username="admin"):
        admin_role = sm.find_role("Admin")
        user = sm.add_user(
            username="admin",
            first_name="Admin",
            last_name="User",
            email="admin@example.com",
            role=admin_role,
            password="admin",
        )
        if user:
            print("Airflow Admin-User erstellt: admin / admin")
        else:
            print("FEHLER: Admin-User konnte nicht erstellt werden!")
    else:
        print("Airflow Admin-User existiert bereits.")
