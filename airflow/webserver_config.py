# Airflow Webserver Configuration with Keycloak OIDC
# Airflow reads variables directly from this file (no function calls needed)

import os
from flask_appbuilder.security.manager import AUTH_OAUTH

# Keycloak connection details (use container hostname for server-side,
# browser uses keycloak:8082 via /etc/hosts)
KEYCLOAK_BASE = os.getenv('KEYCLOAK_URL', 'http://keycloak:8082')
KEYCLOAK_REALM = os.getenv('KEYCLOAK_REALM', 'lakehouse')
KEYCLOAK_OPENID = f'{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/protocol/openid-connect'

# Authentication type
AUTH_TYPE = AUTH_OAUTH

# Allow user self-registration via OAuth
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = 'Viewer'

# Map Keycloak roles to Airflow roles
AUTH_ROLES_MAPPING = {
    'admin': ['Admin'],
    'viewer': ['Viewer'],
    'user': ['User'],
    'op': ['Op'],
}
AUTH_ROLES_SYNC_AT_LOGIN = True

# OAuth provider configuration
OAUTH_PROVIDERS = [
    {
        'name': 'keycloak',
        'icon': 'fa-key',
        'token_key': 'access_token',
        'remote_app': {
            'client_id': os.getenv('KEYCLOAK_CLIENT_ID_AIRFLOW', 'airflow'),
            'client_secret': os.getenv('KEYCLOAK_CLIENT_SECRET_AIRFLOW', ''),
            'api_base_url': f'{KEYCLOAK_OPENID}',
            'client_kwargs': {'scope': 'openid email profile'},
            'access_token_url': f'{KEYCLOAK_OPENID}/token',
            'authorize_url': f'{KEYCLOAK_OPENID}/auth',
            'server_metadata_url': f'{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration',
        },
    },
]
