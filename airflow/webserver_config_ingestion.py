# Flask config for OpenMetadata Ingestion Airflow container.
# Loaded by FAB's create_app() via flask_app.config.from_pyfile().
#
# Disable WTF-CSRF so that the OpenMetadata server can call
# POST /pluginsv2/api/v2/openmetadata/deploy (and /trigger)
# without a CSRF token.  These are machine-to-machine API calls,
# not browser form submissions.
WTF_CSRF_ENABLED = False
