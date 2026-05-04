#!/bin/bash
# Wrapper für das originale elasticsearch Binary
# Fix für cgroupv2 NullPointerException unter Linux 5.x+
#
# Problem: Der JvmOptionsParser crasht weil er cgroup-Speichermetriken liest,
# die unter cgroupv2 nicht korrekt verfügbar sind.
# Lösung: -Des.cgroups.hierarchy.override=/ zum Parser-Aufruf hinzufügen.
#
# Dieses Script ersetzt das originale $ES_HOME/bin/elasticsearch Binary.
# Es wird vom docker-entrypoint.sh aufgerufen, wenn der Container mit "eswrapper" startet.

set -e

ES_HOME=/usr/share/elasticsearch
ES_PATH_CONF=${ES_PATH_CONF:-$ES_HOME/config}

# Environment variables source (JAVA, XSHARE, ES_CLASSPATH, etc.)
source "$ES_HOME/bin/elasticsearch-env"

# Check keystore password
CHECK_KEYSTORE=true
DAEMONIZE=false
for option in "$@"; do
  case "$option" in
    -h|--help|-V|--version)
      CHECK_KEYSTORE=false
      ;;
    -d|--daemonize)
      DAEMONIZE=true
      ;;
  esac
done

# ES_TMPDIR setzen
if [ -z "$ES_TMPDIR" ]; then
  ES_TMPDIR=`"$JAVA" "$XSHARE" -cp "$ES_CLASSPATH" org.elasticsearch.tools.launchers.TempDirectory`
fi

# LIBFFI_TMPDIR setzen
if [ -z "$LIBFFI_TMPDIR" ]; then
  LIBFFI_TMPDIR="$ES_TMPDIR"
  export LIBFFI_TMPDIR
fi

# Keystore password holen
unset KEYSTORE_PASSWORD
KEYSTORE_PASSWORD=
if [[ $CHECK_KEYSTORE = true ]] && \
    "$ES_HOME/bin/elasticsearch-keystore" has-passwd --silent 2>/dev/null; then
  if ! read -s -r -p "Elasticsearch keystore password: " KEYSTORE_PASSWORD; then
    echo "Failed to read keystore password on console" 1>&2
    exit 1
  fi
fi

# JvmOptionsParser AUFRUF – mit -Des.cgroups.hierarchy.override=/ FIX
# Das originale Script ruft den Parser OHNE JVM-Flags auf, was unter cgroupv2 crasht.
# Hier wird der cgroup-hierarchy override explizit gesetzt.
ES_JAVA_OPTS=`export ES_TMPDIR; "$JAVA" "$XSHARE" -Des.cgroups.hierarchy.override=/ -cp "$ES_CLASSPATH" org.elasticsearch.tools.launchers.JvmOptionsParser "$ES_PATH_CONF" "$ES_HOME/plugins"`

# manual parsing to find out, if process should be detached
if [[ $DAEMONIZE = false ]]; then
  exec \
    "$JAVA" \
    "$XSHARE" \
    $ES_JAVA_OPTS \
    -Des.path.home="$ES_HOME" \
    -Des.path.conf="$ES_PATH_CONF" \
    -Des.distribution.flavor="$ES_DISTRIBUTION_FLAVOR" \
    -Des.distribution.type="$ES_DISTRIBUTION_TYPE" \
    -Des.bundled_jdk="$ES_BUNDLED_JDK" \
    -cp "$ES_CLASSPATH" \
    org.elasticsearch.bootstrap.Elasticsearch \
    "$@" <<<"$KEYSTORE_PASSWORD"
else
  exec \
    "$JAVA" \
    "$XSHARE" \
    $ES_JAVA_OPTS \
    -Des.path.home="$ES_HOME" \
    -Des.path.conf="$ES_PATH_CONF" \
    -Des.distribution.flavor="$ES_DISTRIBUTION_FLAVOR" \
    -Des.distribution.type="$ES_DISTRIBUTION_TYPE" \
    -Des.bundled_jdk="$ES_BUNDLED_JDK" \
    -Des.daemonize=true \
    -cp "$ES_CLASSPATH" \
    org.elasticsearch.bootstrap.Elasticsearch \
    "$@" <<<"$KEYSTORE_PASSWORD"
fi
