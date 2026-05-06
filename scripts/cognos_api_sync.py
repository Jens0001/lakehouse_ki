#!/usr/bin/env python3
"""
cognos_api_sync.py – Cognos Analytics API → OpenMetadata Sync
==============================================================

Dieses Skript schließt die letzte Lineage-Lücke im Lakehouse-KI-Stack:
Es verbindet Cognos Analytics (BI-Tool) mit dem OpenMetadata-Datenkatalog,
sodass die vollständige Datenherkunft von der Rohdatenquelle bis zum
Dashboard sichtbar wird.

WORKFLOW:
  1. Cognos REST API → Session aufbauen (anonym oder mit Credentials)
  2. Content-Hierarchie traversieren → Datenmodule und Dashboards suchen
  3. JSON-Definition je Objekt über die API abrufen
  4. Optional: JSON-Dateien lokal exportieren (zur Inspektion / Archivierung)
  5. OpenMetadata-Entities anlegen/aktualisieren (Dashboard Data Models, Charts, Dashboards)
  6. Lineage-Kanten ziehen:
       Trino-Tabelle  →  Dashboard Data Model  →  Dashboard

LINEAGE-KETTE (End-to-End nach erfolgreichem Lauf):
  iceberg.marts.fact_weather_daily  (Trino)
      │  PUT /v1/lineage
      ▼
  cognos_analytics.Lakehouse_KI__fact_weather_daily  (OM Data Model)
      │  dashboard.dataModels[]
      ▼
  cognos_analytics.Temperatur_Durchschniitt  (OM Dashboard)
      │  dashboard.charts[]
      ▼
  cognos_analytics.Temperatur_Durchschniitt__<widget>  (OM Chart)

CLI-OPTIONEN:
  --export          Sucht nach Objekten und exportiert JSONs nach cognos/api_exports/
  --ingest-om       Exportiert UND ingestiert direkt nach OpenMetadata
  --list            Nur auflisten was gefunden würde (kein Export, kein API-Call an OM)
  --diff            Im Export-Modus: nur Objekte exportieren, deren modificationTime
                    neuer ist als die lokale JSON-Datei
  --folder NAME     Abweichender Start-Ordner in Cognos (default: Lakehouse)
  --modules-only    Nur Datenmodule verarbeiten (keine Dashboards)
  --dashboards-only Nur Dashboards/Reports verarbeiten (keine Datenmodule)
  --dry-run         Nichts ändern – zeigt nur an, was getan werden würde
  --cognos-user     Cognos-Benutzername (alternativ: Env-Variable COGNOS_USERNAME)
  --cognos-pass     Cognos-Passwort    (alternativ: Env-Variable COGNOS_PASSWORD)
  -v / --verbose    Debug-Logging aktivieren

UMGEBUNGSVARIABLEN:
  COGNOS_URL      Cognos API-Basis-URL    (default: http://192.168.178.149:9300/api/v1)
  COGNOS_USERNAME Cognos-Benutzername für authentifizierte Sessions (optional)
  COGNOS_PASSWORD Cognos-Passwort        (optional, nur mit COGNOS_USERNAME)
  COGNOS_FOLDER   Start-Ordner in Cognos (default: Lakehouse)
  OM_URL          OpenMetadata API-URL   (default: http://localhost:8585/api)
  OM_TOKEN        Bearer-Token für OpenMetadata (erforderlich für --ingest-om)
  OM_TRINO_SVC    Name des Trino-Datenbankservice in OM (default: lakehouse_trino)
                  → Wird für Lineage-Kanten Trino-Tabelle → DataModel verwendet
  EXPORT_DIR      Lokales Export-Verzeichnis (default: cognos/api_exports)

TYPISCHE AUFRUFE:
    # Verfügbare Objekte auflisten (kein Schreibzugriff):
    python3 scripts/cognos_api_sync.py --list

    # JSON-Dateien lokal exportieren:
    python3 scripts/cognos_api_sync.py --export

    # Nur veränderte Objekte exportieren (schneller bei großen Verzeichnissen):
    python3 scripts/cognos_api_sync.py --export --diff

    # Exportieren + direkt nach OpenMetadata ingestieren:
    export OM_TOKEN='eyJ...'
    python3 scripts/cognos_api_sync.py --ingest-om

    # Nur Datenmodule ingestieren (ohne Dashboards):
    python3 scripts/cognos_api_sync.py --ingest-om --modules-only

    # Anderen Cognos-Ordner als Startpunkt verwenden:
    python3 scripts/cognos_api_sync.py --list --folder "Mein Ordner"

    # Mit Credentials (falls Cognos anonymen Zugriff einschränkt):
    python3 scripts/cognos_api_sync.py --list \\
        --cognos-user admin --cognos-pass geheim

VORAUSSETZUNGEN:
  - Python 3.10+ (nutzt nur Standardbibliothek, keine externen Pakete)
  - Cognos Analytics 11.x oder höher (REST API /api/v1 verfügbar)
  - Für --ingest-om: Trino-Service muss in OM unter OM_TRINO_SVC bereits
    ingested sein, damit Lineage-Kanten zu physischen Tabellen gezogen werden
    können. Fehlende Tabellen werden mit einer Warnung übersprungen.

WICHTIGE API-BESONDERHEITEN (aus Analyse der Cognos-API ermittelt):
  - Content-Antworten nutzen den Key "content" (nicht "entries")
  - Dashboard-Spezifikation: GET /content/{id}?fields=specification
    → "specification" ist ein JSON-String, der noch geparst werden muss
  - Cognos-Dashboards haben den Typ "exploration" (nicht "dashboard")
  - Alle Schreiboperationen (inkl. GET auf /content/{id}/items) benötigen
    den XSRF-Token als Header "X-XSRF-Token" – sonst 403 "X-BI-XSRF: Rejected"
  - useSpec-Felder in Datenmodulen: Datenquelle über "ancestors[0].defaultName"
    und Catalog/Schema über "searchPath" auslesen (nicht über "dataSourceOverride")
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Konfiguration – alle Werte über Umgebungsvariablen überschreibbar
# ---------------------------------------------------------------------------
COGNOS_URL      = os.environ.get("COGNOS_URL",      "http://192.168.178.149:9300/api/v1")
COGNOS_USERNAME = os.environ.get("COGNOS_USERNAME", "")
COGNOS_PASSWORD = os.environ.get("COGNOS_PASSWORD", "")
COGNOS_FOLDER   = os.environ.get("COGNOS_FOLDER",   "Lakehouse")
OM_URL          = os.environ.get("OM_URL",          "http://localhost:8585/api")
OM_TOKEN        = os.environ.get("OM_TOKEN",        "")
# Name des Trino-Datenbankservice in OpenMetadata – muss mit dem in om_setup_connectors.py
# angelegten Service übereinstimmen. Wird für Lineage-Kanten benötigt:
# PUT /v1/lineage: {lakehouse_trino}.iceberg.marts.fact_weather_daily → DataModel
TRINO_SERVICE_FQN = os.environ.get("OM_TRINO_SVC",  "lakehouse_trino")
EXPORT_DIR      = os.environ.get("EXPORT_DIR",      "cognos/api_exports")

# Name des Dashboard-Service in OpenMetadata – unter diesem Service werden alle
# Cognos-Entitäten (Data Models, Charts, Dashboards) angelegt.
OM_SERVICE_NAME     = "cognos_analytics"
# Name der Classification für Cognos-spezifische Spalten-Tags (Identifier/Attribute/Measure)
CLASSIFICATION_NAME = "CognosAnalytics"

log = logging.getLogger("cognos_api_sync")

# ---------------------------------------------------------------------------
# Mapping: Cognos "usage"-Feld → OM Classification-Tag
# ---------------------------------------------------------------------------
# Jede Spalte in einem Cognos Data Module hat eine semantische Rolle:
#   identifier = eindeutiger Schlüssel (z.B. Primärschlüssel, ID-Felder)
#   attribute  = beschreibendes Merkmal (z.B. Name, Kategorie)
#   fact       = Messgröße/KPI (z.B. Umsatz, Temperatur)
# Diese Rollen werden als Classification-Tags in OM übertragen, damit
# der Katalog die semantische Bedeutung der Spalten anzeigt.
_COGNOS_USAGE_MAP: dict[str, str] = {
    "identifier": "Identifier",
    "attribute":  "Attribute",
    "fact":       "Measure",
}

# ---------------------------------------------------------------------------
# Mapping: Cognos Datentyp → OpenMetadata dataType
# ---------------------------------------------------------------------------
# Cognos liefert Datentypen wie "VARCHAR(2147483647)" – wir extrahieren den
# Basistyp (vor der Klammer) und mappen ihn auf den OM-Enum-Wert.
# Unbekannte Typen landen als "STRING" (sicher, verliert keine Daten).
_COGNOS_DATATYPE_MAP: dict[str, str] = {
    "BIGINT": "BIGINT",
    "INTEGER": "INT",
    "INT": "INT",
    "SMALLINT": "SMALLINT",
    "TINYINT": "TINYINT",
    "VARCHAR": "VARCHAR",
    "CHAR": "CHAR",
    "DATE": "DATE",
    "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP_TZ": "TIMESTAMPZ",
    "DECIMAL": "DECIMAL",
    "NUMERIC": "NUMERIC",
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "REAL": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "BLOB": "BLOB",
    "CLOB": "STRING",
    "ARRAY": "ARRAY",
}

# ---------------------------------------------------------------------------
# Mapping: Cognos visId → OpenMetadata ChartType
# ---------------------------------------------------------------------------
# Die visId identifiziert den Diagrammtyp eines Widgets in Cognos Dashboards
# (Exploration). OpenMetadata kennt nur eine begrenzte Menge an ChartTypes –
# nicht direkt mappbare Typen fallen auf "Other" zurück.
_COGNOS_VIS_MAP: dict[str, str] = {
    "com.ibm.vis.rave2bundlecolumn":      "Bar",
    "com.ibm.vis.rave2bundlebar":         "Bar",
    "com.ibm.vis.rave2bundlestackedcolumn": "Bar",
    "com.ibm.vis.rave2line":              "Line",
    "com.ibm.vis.rave2bundlecomposite":   "Other",
    "com.ibm.vis.rave2bundlearea":        "Area",
    "com.ibm.vis.rave2bundlepie":         "Pie",
    "com.ibm.vis.rave2bundlescatter":     "Scatter",
    "com.ibm.vis.rave2bundletreemap":     "Other",
    "com.ibm.vis.rave2bundleheatmap":     "Other",
    "com.ibm.vis.rave2bundletable":       "Table",
    "com.ibm.vis.rave2crosstab":          "Table",
    "summary":                            "Text",
}

# ---------------------------------------------------------------------------
# Cognos Content-Typen
# ---------------------------------------------------------------------------
# Wichtig: Interaktive Dashboards ("Explorations") haben in der Cognos REST API
# den Typ "exploration" – NICHT "dashboard". "dashboard" ist ein älterer,
# seitenbasierter Report-Typ. Beide werden als Dashboard-Entitäten in OM behandelt.
TYPE_MODULE     = "module"
TYPE_REPORT     = "report"
TYPE_DASHBOARD  = "dashboard"
TYPE_FOLDER     = "folder"
TYPE_STORY      = "story"
TYPE_EXPLORATION = "exploration"  # Moderner interaktiver Dashboard-Typ

# Alle Typen, die als OM-Dashboard ingestiert werden sollen
DASHBOARD_TYPES = {TYPE_REPORT, TYPE_DASHBOARD, TYPE_STORY, TYPE_EXPLORATION}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class CognosObject:
    """Repräsentation eines gefundenen Cognos Content-Objekts.

    Wird beim Traversieren der Content-Hierarchie befüllt und enthält alle
    Informationen, die für Export und Ingestion benötigt werden.
    """
    id: str               # Cognos interne ID (z.B. "i3D45C28FAC704060A3820A6AF28E4D5C")
    type: str             # Cognos Typ: "module", "exploration", "report", etc.
    name: str             # Anzeigename (defaultName aus der API)
    path: str             # Pfad im Cognos-Content-Store
    modification_time: str = ""   # ISO-8601, für Diff-Modus
    version: str = ""
    owner: str = ""
    json_data: dict | None = None  # Gefüllter JSON nach dem Abruf der Spezifikation


@dataclass
class SyncStats:
    """Laufende Statistiken für die Zusammenfassung am Ende des Skript-Laufs."""
    found_modules: int = 0        # Gefundene Datenmodule in Cognos
    found_dashboards: int = 0     # Gefundene Dashboards in Cognos
    exported_modules: int = 0     # Lokal als JSON gespeicherte Module
    exported_dashboards: int = 0  # Lokal als JSON gespeicherte Dashboards
    updated_modules: int = 0      # (reserviert für zukünftige Nutzung)
    updated_dashboards: int = 0   # (reserviert für zukünftige Nutzung)
    skipped_unchanged: int = 0    # Im Diff-Modus übersprungene Objekte
    errors: int = 0               # Fehler beim Export oder API-Abruf
    om_created: int = 0           # Neue OM-Entities angelegt
    om_updated: int = 0           # Bestehende OM-Entities aktualisiert
    om_errors: int = 0            # Fehler bei OM-API-Calls
    om_lineage: int = 0           # Erfolgreich angelegte Lineage-Kanten


# ---------------------------------------------------------------------------
# Cognos REST API Client
# ---------------------------------------------------------------------------
class CognosClient:
    """Synchroner HTTP-Client für die Cognos Analytics REST API.

    Kapselt die gesamte HTTP-Kommunikation mit Cognos:
    - Session-Aufbau (anonym oder mit Basic Auth)
    - Cookie-Verwaltung inkl. XSRF-Token-Handling
    - Generischer Request-Dispatcher mit automatischer Cookie-Weitergabe

    WICHTIG: Cognos schützt alle schreibenden Operationen (und auch viele
    lesende wie /items-Endpunkte) mit einem XSRF-Token. Ohne diesen Token
    antwortet die API mit 403 "X-BI-XSRF: Rejected". Der Token wird beim
    Session-Aufbau als Cookie "XSRF-TOKEN" geliefert und muss bei jedem
    folgenden Request als Header "X-XSRF-Token" mitgesendet werden.
    """

    def __init__(self, base_url: str, username: str = "", password: str = "") -> None:
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.cookies: str = ""
        self.xsrf_token: str = ""
        self._session_created: bool = False
        # Cookie-Jar als Liste von (name, value) Tupeln – wird aus Set-Cookie-Headern befüllt
        self._jar: list[tuple[str, str]] = []

    # -- Session Management ------------------------------------------------

    def create_session(self) -> None:
        """Erstellt eine Cognos-Session (anonym oder mit Credentials).

        Ohne Credentials wird eine anonyme Session eröffnet – Cognos erlaubt
        das standardmäßig für öffentlich freigegebene Inhalte. Mit Credentials
        wird zusätzlich ein Login-Schritt durchgeführt.
        """
        if self._session_created:
            return
        if self.username and self.password:
            self._create_authenticated_session()
        else:
            self._create_anonymous_session()

    def _create_anonymous_session(self) -> None:
        """Anonyme Session über GET /session erstellen.

        Cognos liefert dabei Session-Cookies inkl. des XSRF-Tokens, die für
        alle nachfolgenden Requests benötigt werden.
        """
        url = f"{self.base}/session"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                log.info("Session erstellt: anonymous=%s", data.get("isAnonymous", True))
                self._extract_cookies(resp)
                self._session_created = True
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                # 403 beim zweiten Aufruf bedeutet "Session existiert bereits" – kein Fehler
                log.info("Session bereits aktiv (403 beim erneuten Login)")
                self._session_created = True
            else:
                raise

    def _create_authenticated_session(self) -> None:
        """Session mit Basic Authentication erstellen.

        Zwei-Schritt-Prozess:
        1. POST /session  → initialisiert die Session, liefert erste Cookies
        2. GET  /session/login mit Authorization-Header → authentifiziert den User
        """
        # Schritt 1: Session-Cookies holen
        url = f"{self.base}/session"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                log.info("Session erstellt (Basic Auth)")
        except urllib.error.HTTPError:
            pass  # Der Session-Endpunkt kann einen leeren Body oder Fehlercode zurückgeben

        # Schritt 2: Mit Credentials einloggen
        auth_url = f"{self.base}/session/login"
        credentials = f"{self.username}:{self.password}".encode()
        import base64
        auth_header = base64.b64encode(credentials).decode()

        req = urllib.request.Request(auth_url)
        req.add_header("Authorization", f"Basic {auth_header}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                raw = resp.read()
                data = json.loads(raw.decode()) if raw else {}
                log.info("Authentifiziert als: %s", data.get("userId", self.username))
                self._session_created = True
        except urllib.error.HTTPError as exc:
            log.error("Authentifizierung fehlgeschlagen: %d %s", exc.code, exc.read().decode(errors="replace")[:200])
            raise

    def _extract_cookies(self, resp: Any) -> None:
        """Liest alle Set-Cookie-Header aus der Response und speichert sie im Jar.

        Wir verwalten Cookies manuell (statt CookieJar), um volle Kontrolle über
        den XSRF-Token zu haben. Jeder Cookie wird als (name, value) Tupel gespeichert.
        Duplikate werden bewusst zugelassen, da Cognos Cookies bei Bedarf neu setzt.
        """
        for cookie in resp.headers.get_all("Set-Cookie", []):
            # Nur "name=value" extrahieren, Attribute wie Path/Domain/HttpOnly ignorieren
            cookie_part = cookie.split(";")[0].strip()
            if "=" in cookie_part:
                name, value = cookie_part.split("=", 1)
                self._jar.append((name.strip(), value.strip()))

    def _update_session(self) -> None:
        """Stellt sicher, dass Session aktiv und XSRF-Token bekannt ist.

        Wird vor jedem Request aufgerufen. Der XSRF-Token wird lazy aus dem
        Cookie-Jar geladen – er ist erst nach dem ersten Session-Aufbau verfügbar.
        """
        if not self._session_created:
            self.create_session()
        # XSRF-Token aus dem Cookie-Jar lesen (wird beim Session-Aufbau gesetzt)
        if not self.xsrf_token:
            for name, value in self._jar:
                if name == "XSRF-TOKEN":
                    self.xsrf_token = value
                    break

    def _make_request(
        self,
        method: str,
        path: str,
        data: Any = None,
        params: dict | None = None,
    ) -> dict | list | None:
        """Generischer HTTP-Request mit automatischer Cookie- und XSRF-Verwaltung.

        Sendet alle gespeicherten Cookies als "Cookie"-Header mit. Bei Erfolg
        werden neue Cookies aus der Antwort extrahiert (Session-Refresh).
        404 → None (Objekt existiert nicht, kein Fehler).
        """
        self._update_session()
        url = f"{self.base}/{path.lstrip('/')}"

        # Query-Parameter an URL anhängen
        if params:
            query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
            url += f"?{query}"

        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)

        # Cookies zusammenbauen
        if self._jar:
            cookie_header = "; ".join(f"{k}={v}" for k, v in self._jar)
            req.add_header("Cookie", cookie_header)
        if self.xsrf_token:
            req.add_header("X-XSRF-Token", self.xsrf_token)

        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                # Neue Cookies extrahieren
                self._extract_cookies(resp)
                return json.loads(raw.decode()) if raw else None
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace") if exc.fp else ""
            if exc.code == 404:
                return None
            log.error("HTTP %s %s → %d: %s", method, url, exc.code, body_text[:500])
            raise

    # -- Public API Methods ------------------------------------------------

    def get_root_content(self) -> list[dict]:
        """Root-Objekte des Content-Stores abrufen (Teamordner, Eigene Ordner, Bibliothek).

        WICHTIG: Die Cognos REST API gibt die Liste unter dem Key "content" zurück
        (nicht "entries" wie ältere Dokumentationen vermuten lassen).
        """
        result = self._make_request("GET", "/content")
        if isinstance(result, dict) and "content" in result:
            return result["content"]
        return []

    def get_folder_items(self, folder_id: str) -> list[dict]:
        """Alle Objekte innerhalb eines Ordners abrufen.

        Gibt eine flache Liste der direkten Kind-Objekte zurück – keine Rekursion.
        Rekursives Durchsuchen übernimmt ContentExplorer.collect_objects().
        """
        result = self._make_request("GET", f"/content/{folder_id}/items")
        if isinstance(result, dict) and "content" in result:
            return result["content"]
        return []

    def get_content_metadata(self, obj_id: str) -> dict | None:
        """Basisdaten eines Content-Objekts (Name, Typ, Zeitstempel) abrufen."""
        result = self._make_request("GET", f"/content/{obj_id}")
        return result if isinstance(result, dict) else None

    def get_module_definition(self, module_id: str) -> dict | None:
        """Vollständige JSON-Definition eines Datenmoduls abrufen.

        Das Datenmodul-JSON enthält querySubject[], useSpec[], relationship[] etc.
        und ist die Grundlage für die OM-Ingestion (Data Models + Lineage).
        """
        result = self._make_request("GET", f"/modules/{module_id}")
        return result if isinstance(result, dict) else None

    def get_module_metadata(self, module_id: str) -> dict | None:
        """Metadaten eines Datenmoduls abrufen (leichtgewichtiger als die volle Definition)."""
        result = self._make_request("GET", f"/modules/{module_id}/metadata")
        return result if isinstance(result, dict) else None

    def get_dashboard_spec(self, obj_id: str) -> dict | None:
        """Dashboard-Spezifikation abrufen und als fertiges Dict zurückgeben.

        WICHTIG – Zwei API-Besonderheiten:
        1. Der korrekte Query-Parameter lautet "fields=specification" (nicht "extensions=").
        2. Das "specification"-Feld ist ein JSON-String, der noch geparst werden muss.
           Das gilt besonders für den Typ "exploration" (interaktive Dashboards).

        Nach dem Parsen wird das spec-Dict direkt zurückgegeben – es enthält bereits
        die Felder "name", "layout", "dataSources" die von ingest_dashboard() erwartet werden.
        """
        result = self._make_request("GET", f"/content/{obj_id}", params={"fields": "specification"})
        if not isinstance(result, dict):
            return None
        spec = result.get("specification")
        # "exploration"-Dashboards liefern specification als JSON-String → parsen
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                spec = None
        if isinstance(spec, dict):
            # Sicherstellen dass "name" gesetzt ist (Fallback auf defaultName des Content-Objekts)
            if not spec.get("name"):
                spec["name"] = result.get("defaultName", "")
            return spec
        # Für ältere Report-Typen ohne explizites specification-Feld: Wrapper zurückgeben
        result.setdefault("name", result.get("defaultName", ""))
        return result

    def search_by_name(self, name_pattern: str) -> list[dict]:
        """Volltextsuche nach Objekten mit passendem Namen."""
        result = self._make_request("GET", "/search", params={"filter": f"name:~{name_pattern}"})
        if isinstance(result, dict):
            # "content" ist der Standard-Key; "entries" als Fallback für ältere API-Versionen
            return result.get("content", result.get("entries", []))
        return []


# ---------------------------------------------------------------------------
# Content Explorer – rekursive Traversierung der Cognos-Hierarchie
# ---------------------------------------------------------------------------
class ContentExplorer:
    """Traversiert die Cognos Content-Hierarchie rekursiv.

    Cognos organisiert Inhalte in einer Ordnerstruktur. Um alle relevanten
    Objekte (Module, Dashboards) zu finden, müssen alle Unterordner rekursiv
    durchsucht werden. Dabei werden nur Objekte der gesuchten Typen gesammelt,
    Ordner selbst werden nicht als Ergebnis zurückgegeben.
    """

    def __init__(self, client: CognosClient) -> None:
        self.client = client

    def find_folder_by_name(self, target_name: str, start_id: str = "") -> str | None:
        """Sucht rekursiv nach einem Ordner mit passendem Namen. Gibt die Cognos-ID zurück.

        Die Suche ist case-insensitiv und prüft ob target_name im Ordnernamen enthalten
        ist (kein exakter Match). Beispiel: "lake" würde "Lakehouse" finden.
        """
        items = self.client.get_root_content() if not start_id else self.client.get_folder_items(start_id)

        for item in items:
            obj_type = item.get("type", "")
            obj_name = item.get("defaultName", item.get("name", "")).lower()
            obj_id = item.get("id", "")

            if obj_type == TYPE_FOLDER and target_name.lower() in obj_name:
                return obj_id

            if obj_type == TYPE_FOLDER:
                # Tiefer suchen – Cognos kann beliebig tief verschachtelt sein
                found = self.find_folder_by_name(target_name, obj_id)
                if found:
                    return found

        return None

    def collect_objects(
        self,
        folder_id: str,
        types: set[str] | None = None,
    ) -> list[CognosObject]:
        """Sammelt alle Objekte des gewünschten Typs aus einem Ordner inkl. Unterordnern.

        Durchsucht die Hierarchie rekursiv. Ordner werden nicht als Ergebnis
        zurückgegeben, sondern nur zur Rekursion genutzt.
        Gibt eine flache Liste aller gefundenen CognosObject-Instanzen zurück.
        """
        if types is None:
            types = {TYPE_MODULE, *DASHBOARD_TYPES}

        objects: list[CognosObject] = []
        items = self.client.get_folder_items(folder_id)

        for item in items:
            obj_type = item.get("type", "")
            obj_id = item.get("id", "")
            obj_name = item.get("defaultName", item.get("name", "unnamed"))
            mod_time = item.get("modificationTime", "")
            version = item.get("version", "")
            owner = item.get("owner", "")
            path = item.get("objectPath", f"/{obj_name}")

            if obj_type in types:
                objects.append(CognosObject(
                    id=obj_id, type=obj_type, name=obj_name, path=path,
                    modification_time=mod_time, version=version, owner=owner,
                ))
            elif obj_type == TYPE_FOLDER:
                # Unterordner rekursiv durchsuchen
                objects.extend(self.collect_objects(obj_id, types))

        return objects


# ---------------------------------------------------------------------------
# JSON Export Manager
# ---------------------------------------------------------------------------
class ExportManager:
    """Verwaltet das lokale Exportieren von Cognos-JSON-Dateien.

    Legt Dateien unter {base_dir}/{folder_name}/modules/ und /dashboards/ ab.
    Der Dateiname setzt sich aus sanitisiertem Objektnamen + letzten 8 Zeichen
    der Cognos-ID zusammen, um Namenskollisionen zu vermeiden.

    Im Diff-Modus (--diff) wird jede Datei nur dann neu geschrieben, wenn
    die modificationTime in Cognos neuer ist als der Timestamp der lokalen Datei.
    """

    def __init__(self, base_dir: str, diff_mode: bool = False) -> None:
        self.base_dir = Path(base_dir)
        self.diff_mode = diff_mode
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_name(self, name: str) -> str:
        """Ersetzt ungültige Zeichen im Dateinamen."""
        sanitized = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name.lower())
        return sanitized.strip("_")

    def get_export_path(self, obj: CognosObject, is_module: bool) -> Path:
        """Gibt den Export-Pfad für ein Objekt zurück."""
        category = "modules" if is_module else "dashboards"
        folder_name = self._sanitize_name(COGNOS_FOLDER)
        dir_path = self.base_dir / folder_name / category

        # ID-basierter Dateiname für Eindeutigkeit
        safe_name = self._sanitize_name(obj.name)
        return dir_path / f"{safe_name}_{obj.id[-8:]}.json"

    def should_export(self, path: Path, modification_time: str) -> bool:
        """Prüft ob ein Export nötig ist (Diff-Modus)."""
        if not self.diff_mode:
            return True
        if not path.exists():
            return True
        # Vergleich basierend auf modificationTime
        try:
            file_mtime = datetime.datetime.fromtimestamp(
                path.stat().st_mtime, tz=datetime.timezone.utc
            ).isoformat()
            # modification_time kommt als ISO-8601 von Cognos
            return modification_time != file_mtime[:19]
        except OSError:
            return True

    def export(self, obj: CognosObject, data: dict, is_module: bool) -> Path:
        """Schreibt die JSON-Datei."""
        path = self.get_export_path(obj, is_module)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Metadaten hinzufügen
        export_data = {
            "_metadata": {
                "cognos_id": obj.id,
                "cognos_type": obj.type,
                "cognos_name": obj.name,
                "cognos_path": obj.path,
                "cognos_modification_time": obj.modification_time,
                "cognos_version": obj.version,
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            **data,
        }

        path.write_text(json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("  → Exportiert: %s", path)
        return path


# ---------------------------------------------------------------------------
# OpenMetadata Ingestion Client
# ---------------------------------------------------------------------------
class OMClient:
    """Synchroner HTTP-Client für die OpenMetadata REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base = base_url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.token = token

    def _request(
        self, method: str, path: str, data: Any = None
    ) -> dict | list | None:
        url = f"{self.base}/{path.lstrip('/')}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return json.loads(raw.decode()) if raw else None
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace") if exc.fp else ""
            if exc.code == 404:
                log.warning("OM HTTP %s %s → 404 (nicht gefunden): %s", method, url, body_text[:200])
                return None
            log.error("OM HTTP %s %s → %d: %s", method, url, exc.code, body_text[:500])
            raise

    def get(self, path: str) -> dict | None:
        result = self._request("GET", path)
        return result if isinstance(result, dict) else None

    def put(self, path: str, data: Any) -> dict | None:
        result = self._request("PUT", path, data)
        return result if isinstance(result, dict) else None

    def post(self, path: str, data: Any) -> dict | None:
        result = self._request("POST", path, data)
        return result if isinstance(result, dict) else None

    def patch(self, path: str, data: Any) -> dict | None:
        result = self._request("PATCH", path, data)
        return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# OpenMetadata Ingester für Cognos
# ---------------------------------------------------------------------------
class CognosOMIngester:
    """Ingestet Cognos Data Modules und Dashboards nach OpenMetadata."""

    CLASSIFICATION_TAGS = {
        "Identifier": "Cognos Usage: Identifier – Eindeutiges Identifikationsmerkmal",
        "Attribute":  "Cognos Usage: Attribute – Beschreibendes Merkmal",
        "Measure":    "Cognos Usage: Measure/Fact – Messgröße",
    }

    def __init__(self, client: OMClient, trino_svc: str = TRINO_SERVICE_FQN) -> None:
        self.client = client
        self.trino_svc = trino_svc
        self._created = 0
        self._updated = 0
        self._errors = 0
        self._lineage = 0

    def _map_datatype(self, cognos_type: str) -> tuple[str, str]:
        raw = cognos_type.strip().upper()
        base = re.split(r"[(\s]", raw)[0]
        om_type = _COGNOS_DATATYPE_MAP.get(base, "STRING")
        return om_type, cognos_type

    # -- Setup ---------------------------------------------------------------

    def ensure_dashboard_service(self) -> None:
        existing = self.client.get(f"v1/services/dashboardServices/name/{OM_SERVICE_NAME}")
        if existing:
            log.info("Dashboard-Service '%s' existiert bereits", OM_SERVICE_NAME)
            return

        payload = {
            "name": OM_SERVICE_NAME,
            "displayName": "Cognos Analytics",
            "description": "IBM Cognos Analytics – Automatisch ingestiert.",
            "serviceType": "CustomDashboard",
            "connection": {
                "config": {
                    "type": "CustomDashboard",
                    "sourcePythonClass": "cognos_api_sync",
                    "dashboardUrl": COGNOS_URL.replace("/api/v1", ""),
                }
            },
        }
        self.client.put("v1/services/dashboardServices", payload)
        log.info("Dashboard-Service '%s' erstellt", OM_SERVICE_NAME)

    def ensure_classification_and_tags(self) -> None:
        existing = self.client.get(f"v1/classifications/name/{CLASSIFICATION_NAME}")
        if not existing:
            self.client.put("v1/classifications", {
                "name": CLASSIFICATION_NAME,
                "displayName": "Cognos Analytics",
                "description": "Tags für Cognos Analytics Datenmodul-Metadaten.",
            })
            log.info("Classification '%s' erstellt", CLASSIFICATION_NAME)

        for tag_name, tag_desc in self.CLASSIFICATION_TAGS.items():
            tag_existing = self.client.get(f"v1/tags/name/{CLASSIFICATION_NAME}.{tag_name}")
            if not tag_existing:
                self.client.post("v1/tags", {
                    "name": tag_name,
                    "displayName": tag_name,
                    "description": tag_desc,
                    "classification": CLASSIFICATION_NAME,
                })

    # -- Data Model Ingestion ------------------------------------------------

    def _build_columns(self, items: list[dict]) -> list[dict]:
        """Wandelt Cognos queryItem-Objekte in OM-Column-Definitionen um.

        Jeder Eintrag in items hat die Struktur {"queryItem": {...}}.
        Aus dem queryItem werden Name, Datentyp, semantische Rolle (usage)
        und Aggregationsfunktion extrahiert und als OM-Column-Dict aufbereitet.

        Spalten mit bekannter "usage"-Rolle (identifier/attribute/fact) erhalten
        einen Classification-Tag, der in OM die semantische Bedeutung anzeigt.
        """
        columns = []
        for item_wrapper in items:
            qi = item_wrapper.get("queryItem")
            if not qi:
                continue  # Nicht-Column-Einträge (z.B. Folder) überspringen
            om_type, display_type = self._map_datatype(qi.get("datatype", "VARCHAR"))
            usage = qi.get("usage", "")
            tag_name = _COGNOS_USAGE_MAP.get(usage)

            # Beschreibung aus Cognos-Metadaten zusammensetzen
            desc_parts = []
            if qi.get("label") and qi.get("label") != qi.get("identifier"):
                desc_parts.append(f"Label: {qi['label']}")
            desc_parts.append(f"Usage: {usage}")
            agg = qi.get("regularAggregate", "")
            if agg:
                desc_parts.append(f"Aggregation: {agg}")

            col: dict[str, Any] = {
                "name": qi.get("identifier", qi.get("expression", "unknown")),
                "displayName": qi.get("label", qi.get("identifier", "")),
                "dataType": om_type,
                "dataTypeDisplay": display_type,  # Originaler Cognos-Typ (z.B. "VARCHAR(2147483647)")
                "description": " | ".join(desc_parts),
            }

            # VARCHAR/CHAR/BINARY/VARBINARY erfordern dataLength – aus Cognos-Typ extrahieren
            if om_type in {"VARCHAR", "CHAR", "BINARY", "VARBINARY"}:
                m = re.search(r'\((\d+)\)', display_type)
                col["dataLength"] = int(m.group(1)) if m else 255

            if tag_name:
                # Classification-Tag setzt das semantische Label in OM
                col["tags"] = [{
                    "tagFQN": f"{CLASSIFICATION_NAME}.{tag_name}",
                    "source": "Classification",
                    "labelType": "Automated",
                    "state": "Confirmed",
                }]
            columns.append(col)
        return columns

    def ingest_data_model(
        self,
        qs: dict,
        module_name: str,
        module_label: str,
        datasource_name: str,
        catalog: str,
        schema: str,
    ) -> str | None:
        """Legt einen OM Dashboard Data Model für ein Cognos Query Subject an (Upsert).

        Namensschema: {module_name}__{qs_identifier}
        Beispiel: Lakehouse_KI__fact_weather_daily

        Gibt die OM-Entity-ID zurück – die wird anschließend für die Lineage-Kante
        Trino-Tabelle → Data Model benötigt. Rückgabe None bei Fehler.
        """
        qs_id = qs.get("identifier", "unknown")
        qs_label = qs.get("label", qs_id)
        model_name = f"{module_name}__{qs_id}"
        columns = self._build_columns(qs.get("item", []))

        description = (
            f"**Cognos Data Module**: {module_label}\n"
            f"**Datenquelle**: {datasource_name} → `{catalog}.{schema}`\n"
            f"**Query Subject**: {qs_id}"
        )

        payload = {
            "name": model_name,
            "displayName": f"{qs_label} ({module_label})",
            "description": description,
            "service": OM_SERVICE_NAME,
            "dataModelType": "LookMlView",
            "columns": columns,
        }

        try:
            result = self.client.put("v1/dashboard/datamodels", payload)
            if not result:
                self._errors += 1
                log.error("    ✗ Leere Antwort bei Data Model '%s'", model_name)
                return None
            fqn = result.get("fullyQualifiedName", "")
            # OM liefert version=0.1 für neu angelegte Entities, >0.1 für Updates
            if result.get("version", 0.0) <= 0.1:
                self._created += 1
                log.info("    ✓ Data Model erstellt: %s", fqn)
            else:
                self._updated += 1
                log.info("    ↻ Data Model aktualisiert: %s", fqn)
            return result.get("id")
        except urllib.error.HTTPError as exc:
            self._errors += 1
            log.error("    ✗ Fehler bei Data Model '%s': %s", model_name, exc)
            return None

    # -- Lineage ---------------------------------------------------------------

    def create_lineage_to_trino(
        self, data_model_id: str, table_name: str, catalog: str, schema: str
    ) -> None:
        """Zieht eine Lineage-Kante von der physischen Trino-Tabelle zum OM Data Model.

        Voraussetzung: Die Trino-Tabelle muss bereits im Katalog unter dem Service
        TRINO_SERVICE_FQN (default: "lakehouse_trino") ingested sein. Ist die Tabelle
        nicht vorhanden, wird die Lineage-Kante mit einer Warnung übersprungen –
        das Skript bricht nicht ab.

        Payload-Format: PUT /v1/lineage
          { "edge": { "fromEntity": {id, type: "table"},
                      "toEntity":   {id, type: "dashboardDataModel"} } }
        """
        table_fqn = f"{self.trino_svc}.{catalog}.{schema}.{table_name}"
        table_entity = self.client.get(f"v1/tables/name/{table_fqn}")
        if not table_entity:
            log.warning("    ⚠ Trino-Tabelle nicht gefunden: %s (Lineage übersprungen)", table_fqn)
            return
        lineage_payload = {
            "edge": {
                "fromEntity": {"id": table_entity["id"], "type": "table"},
                "toEntity":   {"id": data_model_id,      "type": "dashboardDataModel"},
            },
        }
        try:
            self.client.put("v1/lineage", lineage_payload)
            self._lineage += 1
            log.info("    → Lineage: %s → DataModel", table_fqn)
        except urllib.error.HTTPError as exc:
            log.warning("    ⚠ Lineage-Fehler für %s: %s", table_fqn, exc)

    def ingest_module(self, module_data: dict) -> None:
        """Ingestiert ein Cognos Datenmodul vollständig nach OpenMetadata.

        Pro Query Subject im Modul wird ein OM Dashboard Data Model angelegt.
        Danach wird für jedes Data Model eine Lineage-Kante zur physischen
        Trino-Tabelle erstellt.

        WICHTIG – useSpec-Struktur in Cognos:
        Das Modul referenziert seine Datenquellen nicht über ein einfaches Feld,
        sondern über useSpec[]-Einträge mit einer "searchPath"-Eigenschaft:
            searchPath: ".../dataSourceSchema[@name='iceberg/marts']"
        Catalog und Schema werden per Regex aus dem searchPath extrahiert.
        Der Datenquellen-Name steht in "ancestors[0].defaultName".

        Jedes querySubject hat ein "ref"-Feld wie ["M1.fact_weather_daily"],
        wobei "M1" die useSpec-ID und "fact_weather_daily" der Tabellenname ist.
        Darüber wird der physische Tabellenname für die Lineage-Kante ermittelt.
        """
        identifier = module_data.get("identifier", "unknown")
        label = module_data.get("label", identifier)
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", label).strip("_")

        # useSpec-Lookup aufbauen: {identifier → {name, catalog, schema}}
        # searchPath-Format: CAMID(":")/dataSource[@name='...']/dataSourceSchema[@name='iceberg/marts']
        use_spec_map: dict[str, dict] = {}
        for us in module_data.get("useSpec", []):
            us_id = us.get("identifier", "")
            ancestors = us.get("ancestors") or []
            ds_name = ancestors[0].get("defaultName", "Unknown") if ancestors else "Unknown"
            sp = us.get("searchPath", "")
            # Schema-Name aus searchPath extrahieren (Format: "iceberg/marts")
            m = re.search(r"dataSourceSchema\[@name='([^']+)'\]", sp)
            raw_schema = m.group(1) if m else ""
            catalog, _, schema = raw_schema.partition("/")
            use_spec_map[us_id] = {
                "name": ds_name,
                "catalog": catalog or "iceberg",
                "schema": schema or "default",
            }

        log.info("  Ingest Data Module: %s (%s)", label, identifier)
        for us_id, info in use_spec_map.items():
            log.info("    DS %s: %s → %s.%s", us_id, info["name"], info["catalog"], info["schema"])

        self.ensure_dashboard_service()
        self.ensure_classification_and_tags()

        for qs in module_data.get("querySubject", []):
            qs_id = qs.get("identifier", "unknown")
            # ref-Feld: ["M1.artist_chart_performance"] → useSpec-ID "M1", Tabellenname "artist_chart_performance"
            refs = qs.get("ref") or []
            us_key    = refs[0].split(".")[0]     if refs else ""
            table_name = refs[0].split(".", 1)[-1] if refs else ""
            us_info = use_spec_map.get(us_key) or {"name": "Unknown", "catalog": "iceberg", "schema": "default"}
            log.info("    Verarbeite Query Subject: %s (DS: %s → %s.%s)",
                     qs_id, us_info["name"], us_info["catalog"], us_info["schema"])
            model_id = self.ingest_data_model(
                qs, sanitized, label, us_info["name"], us_info["catalog"], us_info["schema"]
            )
            # Lineage nur wenn Data Model erfolgreich angelegt und Tabellenname bekannt
            if model_id and table_name:
                self.create_lineage_to_trino(model_id, table_name, us_info["catalog"], us_info["schema"])

    # -- Dashboard Ingestion -----------------------------------------------

    def _extract_widgets(self, node: dict) -> list[dict]:
        """Sucht rekursiv alle Widgets in einem Layout-Knoten.

        Das Cognos Dashboard-Layout ist ein verschachtelter Baum aus Containern,
        Pages, Tabs und Widgets. Widgets sind die Blätter des Baums – sie enthalten
        die eigentlichen Visualisierungen. Alle anderen Knoten werden nur zur
        Rekursion genutzt.
        """
        widgets = []
        for child in node.get("items", []):
            if child.get("type") == "widget":
                w = self._parse_widget(child)
                if w:
                    widgets.append(w)
            elif "items" in child:
                # Tiefer suchen (container, genericPage, tab, etc.)
                widgets.extend(self._extract_widgets(child))
        return widgets

    def _parse_widget(self, node: dict) -> dict | None:
        """Liest Visualisierungstyp, Name und verwendete Spalten aus einem Widget-Knoten.

        Relevante Daten stecken in features.Models_internal:
        - visId: Diagrammtyp (z.B. "com.ibm.vis.rave2line")
        - name.translationTable.Default: Widgetname in der Default-Sprache
        - data.dataViews[].dataItems[].itemId: Referenzierte Spalten aus dem Datenmodul
          Format: "querySubject.spaltenname" (z.B. "fact_weather_daily.temperature_avg")
          Interne Spalten (beginnen mit "_") werden herausgefiltert.

        Gibt None zurück wenn kein Models_internal vorhanden ist (z.B. reine Text-/Bild-Widgets).
        """
        features = node.get("features", {})
        model = features.get("Models_internal", {})
        if not model:
            return None

        vis_id = model.get("visId", "")
        name_table = model.get("name", {}).get("translationTable", {})
        widget_name = name_table.get("Default", "")

        # Alle referenzierten Spalten sammeln (Format: "querySubject.column")
        columns: list[str] = []
        data = model.get("data", {})
        for dv in data.get("dataViews", []):
            for di in dv.get("dataItems", []):
                item_id = di.get("itemId", "")
                if item_id and not item_id.startswith("_"):  # "_"-Präfix = interne Cognos-Felder
                    columns.append(item_id)

        return {
            "id": node.get("id", ""),
            "name": widget_name,
            "visId": vis_id,
            "chartType": _COGNOS_VIS_MAP.get(vis_id, "Other"),
            "columns": columns,
        }

    def _extract_tabs(self, layout: dict) -> list[dict]:
        """Extrahiert Tab-Struktur aus dem Dashboard-Layout für die OM-Beschreibung.

        Das Layout-Objekt selbst hat type="tab"; seine direkten Kinder sind "container"-Knoten
        die den einzelnen Dashboard-Seiten entsprechen. _extract_widgets() findet dann die
        Widgets rekursiv innerhalb jedes Containers.
        """
        tabs = []
        for item in layout.get("items", []):
            if item.get("type") == "container":
                title_table = item.get("title", {}).get("translationTable", {})
                tab_title = title_table.get("Default", "")
                tab_widgets = self._extract_widgets(item)
                tabs.append({"title": tab_title, "widgets": tab_widgets})
            elif item.get("type") == "widget":
                # Widget direkt auf Root-Ebene (kein Tab-Container)
                w = self._parse_widget(item)
                if w:
                    tabs.append({"title": "Widget", "widgets": [w]})
        return tabs

    def ingest_dashboard(self, dashboard_data: dict) -> None:
        """Ingestiert ein Cognos Dashboard nach OpenMetadata.

        Erstellt pro Widget einen OM Chart und dann ein OM Dashboard, das auf
        alle Charts verweist. Zusätzlich wird das Dashboard mit den verwendeten
        OM Data Models verknüpft (über das dataModels[]-Feld), was in OM die
        Lineage-Kante Dashboard → Data Model ergibt.

        Widget-Spalten haben das Format "querySubject.column" – der QuerySubject-Name
        vor dem Punkt wird verwendet, um den passenden Data Model FQN zu konstruieren:
          fact_weather_daily.temperature_avg → cognos_analytics.model.Lakehouse_KI__fact_weather_daily
        """
        dash_name = dashboard_data.get("name", "Unbekanntes Dashboard")
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", dash_name).strip("_")

        layout = dashboard_data.get("layout", {})
        tabs = self._extract_tabs(layout)
        all_widgets = [w for tab in tabs for w in tab.get("widgets", [])]

        # Referenzierte Datenmodule aus Dashboard-Metadaten auslesen
        sources_section = dashboard_data.get("dataSources", {})
        sources = sources_section.get("sources", [])
        source_names = [s.get("name", "") for s in sources if s.get("name")]

        log.info("  Ingest Dashboard: %s", dash_name)
        log.info("    Tabs: %d, Widgets: %d", len(tabs), len(all_widgets))
        log.info("    Datenmodule: %s", ", ".join(source_names) or "keine")

        self.ensure_dashboard_service()

        # Pro Widget einen OM Chart anlegen
        chart_fqns = []
        for widget in all_widgets:
            chart_name = f"{sanitized}__{re.sub(r'[^a-zA-Z0-9_]+', '_', widget.get('name', '')).strip('_') or widget.get('id', '')}"
            payload = {
                "name": chart_name,
                "displayName": widget.get("name", widget.get("id", "")),
                "description": f"**Dashboard**: {dash_name}\n**Typ**: {widget.get('chartType', 'Other')}",
                "service": OM_SERVICE_NAME,
                "chartType": widget.get("chartType", "Other"),
            }
            try:
                result = self.client.put("v1/charts", payload)
                if not result:
                    self._errors += 1
                    log.error("    ✗ Leere Antwort bei Chart '%s'", chart_name)
                    continue
                fqn = result.get("fullyQualifiedName", "")
                if result.get("version", 0.0) <= 0.1:
                    self._created += 1
                else:
                    self._updated += 1
                chart_fqns.append(fqn)
            except urllib.error.HTTPError as exc:
                self._errors += 1
                log.error("    ✗ Chart-Fehler: %s", exc)

        # DataModel-FQNs aus Widget-Columns ableiten (für Lineage in OM)
        # Widget-Columns haben Format "querySubject.column" → QuerySubject-Name extrahieren
        qs_names: set[str] = set()
        for widget in all_widgets:
            for col in widget.get("columns", []):
                if "." in col:
                    qs_names.add(col.split(".")[0])
        data_model_fqns: list[str] = []
        for source_name in source_names:
            sanitized_mod = re.sub(r"[^a-zA-Z0-9_]+", "_", source_name).strip("_")
            for qs_name in sorted(qs_names):
                data_model_fqns.append(f"{OM_SERVICE_NAME}.model.{sanitized_mod}__{qs_name}")
        if data_model_fqns:
            log.info("    DataModels verknüpft: %s", ", ".join(data_model_fqns))

        # Dashboard erstellen
        description = f"**Cognos Dashboard**: {dash_name}\n**Datenmodule**: {', '.join(source_names) or 'keine'}"
        if tabs:
            description += "\n\n### Tabs\n"
            for tab in tabs:
                wnames = [w.get("name", "?") for w in tab.get("widgets", [])]
                description += f"- **{tab.get('title', '?')}** ({len(wnames)} Widgets)\n"

        payload = {
            "name": sanitized,
            "displayName": dash_name,
            "description": description,
            "service": OM_SERVICE_NAME,
            "charts": chart_fqns,
            "dataModels": data_model_fqns,
        }

        try:
            result = self.client.put("v1/dashboards", payload)
            if not result:
                self._errors += 1
                log.error("  ✗ Leere Antwort bei Dashboard '%s'", sanitized)
                return
            fqn = result.get("fullyQualifiedName", "")
            if result.get("version", 0.0) <= 0.1:
                self._created += 1
                log.info("  ✓ Dashboard erstellt: %s", fqn)
            else:
                self._updated += 1
                log.info("  ↻ Dashboard aktualisiert: %s", fqn)
        except urllib.error.HTTPError as exc:
            self._errors += 1
            log.error("  ✗ Dashboard-Fehler: %s", exc)


# ---------------------------------------------------------------------------
# Main Sync Logic
# ---------------------------------------------------------------------------
def run_sync(
    cognos_client: CognosClient,
    folder_name: str,
    stats: SyncStats,
    export_manager: ExportManager,
    do_export: bool,
    do_ingest: bool,
    do_list: bool,
    types_filter: set[str] | None,
    om_client: OMClient | None,
) -> None:
    """Haupt-Sync-Logik."""

    # 1. Content-Explorer starten
    explorer = ContentExplorer(cognos_client)

    # 2. Folder-ID finden
    log.info("Suche Ordner: '%s'", folder_name)
    folder_id = explorer.find_folder_by_name(folder_name)
    if not folder_id:
        # Liste Root auf zur Fehlersuche
        root = cognos_client.get_root_content()
        root_names = [f"{i.get('type')}/{i.get('defaultName', '?')}" for i in root[:20]]
        log.error("Ordner '%s' nicht gefunden!", folder_name)
        log.info("Verfügbare Root-Objekte (erste 20):")
        for name in root_names:
            log.info("  - %s", name)
        stats.errors += 1
        return

    log.info("Ordner gefunden: ID=%s", folder_id[-8:])

    # 3. Objects sammeln
    log.info("Sammelt Objekte aus Ordner '%s'...", folder_name)
    objects = explorer.collect_objects(folder_id, types_filter)

    modules = [o for o in objects if o.type == TYPE_MODULE]
    dashboards = [o for o in objects if o.type in DASHBOARD_TYPES]

    stats.found_modules = len(modules)
    stats.found_dashboards = len(dashboards)

    log.info("Gefunden: %d Module, %d Dashboards/Reports", len(modules), len(dashboards))

    if do_list:
        log.info("")
        log.info("=" * 70)
        log.info("LISTE DER GEFUNDENEN OBJEKTE")
        log.info("=" * 70)
        for m in modules:
            log.info("[MODULE] %s  (id=%s, modified=%s)", m.name, m.id[-8:], m.modification_time)
        for d in dashboards:
            log.info("[DASHBOARD] %s  (id=%s, modified=%s)", d.name, d.id[-8:], d.modification_time)
        log.info("=" * 70)
        return

    # Einzigen Ingester für den gesamten Lauf erstellen
    ingester = CognosOMIngester(om_client) if (do_ingest and om_client) else None

    # 4. Datenmodule exportieren und ingestieren
    for mod_obj in modules:
        try:
            log.info("Verarbeite Module: %s (id=%s)", mod_obj.name, mod_obj.id[-8:])
            module_data = cognos_client.get_module_definition(mod_obj.id)
            if not module_data:
                log.warning("  Konnte JSON nicht abrufen, überspringe")
                stats.errors += 1
                continue

            # JSON speichern
            if do_export:
                path = export_manager.export(mod_obj, module_data, is_module=True)
                stats.exported_modules += 1
                log.info("  → JSON gespeichert: %s", path)
            else:
                path = ""

            # Nach OpenMetadata ingestieren
            if ingester:
                ingester.ingest_module(module_data)

        except Exception as exc:
            stats.errors += 1
            log.error("  ✗ Fehler bei Module '%s': %s", mod_obj.name, exc)

    # 5. Dashboards exportieren und ingestieren
    for dash_obj in dashboards:
        try:
            log.info("Verarbeite Dashboard: %s (id=%s)", dash_obj.name, dash_obj.id[-8:])
            dash_data = cognos_client.get_dashboard_spec(dash_obj.id)
            if not dash_data:
                log.warning("  Konnte JSON nicht abrufen, überspringe")
                stats.errors += 1
                continue

            # JSON speichern
            if do_export:
                path = export_manager.export(dash_obj, dash_data, is_module=False)
                stats.exported_dashboards += 1
                log.info("  → JSON gespeichert: %s", path)
            else:
                path = ""

            # Nach OpenMetadata ingestieren
            if ingester:
                ingester.ingest_dashboard(dash_data)

        except Exception as exc:
            stats.errors += 1
            log.error("  ✗ Fehler bei Dashboard '%s': %s", dash_obj.name, exc)

    # Ingester-Stats in SyncStats übertragen
    if ingester:
        stats.om_created = ingester._created
        stats.om_updated = ingester._updated
        stats.om_errors = ingester._errors
        stats.om_lineage = ingester._lineage


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cognos Analytics API Sync – JSON Export + OpenMetadata Ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
    # Nur auflisten was gefunden würde:
    python3 scripts/cognos_api_sync.py --list

    # JSONs exportieren:
    python3 scripts/cognos_api_sync.py --export

    # Exportieren + nach OpenMetadata ingestieren:
    export OM_TOKEN='eyJ...'
    python3 scripts/cognos_api_sync.py --ingest-om

    # Nur Datenmodule, Diff-Modus:
    python3 scripts/cognos_api_sync.py --export --diff --modules-only

    # Anderer Ordner:
    python3 scripts/cognos_api_sync.py --export --folder "MeinOrdner"

Umgebungsvariablen:
    COGNOS_URL     Cognos API URL       (default: http://192.168.178.149:9300/api/v1)
    COGNOS_FOLDER  Start-Ordner          (default: Lakehouse)
    OM_URL         OpenMetadata API URL  (default: http://localhost:8585/api)
    OM_TOKEN       OpenMetadata Auth Token
    OM_TRINO_SVC   Trino-Service-Name in OM (default: lakehouse_trino)
        """,
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="JSON-Dateien exportieren",
    )
    parser.add_argument(
        "--ingest-om",
        action="store_true",
        help="Exportieren UND nach OpenMetadata ingestieren",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Nur auflisten was gefunden würde",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Nur neu/geänderte Objekte exportieren",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Anderer Start-Ordner (default: aus COGNOS_FOLDER oder 'Lakehouse')",
    )
    parser.add_argument(
        "--modules-only",
        action="store_true",
        help="Nur Datenmodule verarbeiten",
    )
    parser.add_argument(
        "--dashboards-only",
        action="store_true",
        help="Nur Dashboards/Reports verarbeiten",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nicht ändern, nur anzeigen",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Debug-Logging",
    )
    parser.add_argument(
        "--cognos-user",
        type=str,
        default=None,
        help="Cognos Benutzername (oder über COGNOS_USERNAME)",
    )
    parser.add_argument(
        "--cognos-pass",
        type=str,
        default=None,
        help="Cognos Passwort (oder über COGNOS_PASSWORD)",
    )
    args = parser.parse_args()

    # Credentials合并 mit Environment Variables
    username = args.cognos_user or COGNOS_USERNAME
    password = args.cognos_pass or COGNOS_PASSWORD

    # Logging konfigurieren
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Typ-Filter setzen
    if args.modules_only:
        types_filter = {TYPE_MODULE}
    elif args.dashboards_only:
        types_filter = DASHBOARD_TYPES.copy()
    else:
        types_filter = {TYPE_MODULE, *DASHBOARD_TYPES}

    # Ordner-Name
    folder_name = args.folder or COGNOS_FOLDER

    # Stats
    stats = SyncStats()

    log.info("=" * 70)
    log.info("Cognos API Sync")
    log.info("  Cognos URL:     %s", COGNOS_URL)
    log.info("  Authentifiziert: %s", "Ja" if username else "Nein (anonym)")
    log.info("  Ordner:         %s", folder_name)
    log.info("  Export-Dir:     %s", EXPORT_DIR)
    log.info("  Aktionen:       export=%s, ingest-om=%s, list=%s, diff=%s",
             args.export or args.ingest_om, args.ingest_om, args.list, args.diff)
    log.info("=" * 70)

    # Dry-Run: Kein echter API-Call nötig
    if args.dry_run:
        log.info("DRY RUN – keine Änderungen")
        # Immer noch Session aufbauen für Info
        client = CognosClient(COGNOS_URL)
        client.create_session()
        log.info("Cognos API erreichbar.")
        return

    # 1. Cognos Client
    cognos_client = CognosClient(COGNOS_URL, username, password)
    cognos_client.create_session()

    # 2. Export Manager
    export_manager = ExportManager(EXPORT_DIR, diff_mode=args.diff)

    # 3. OpenMetadata Client (optional)
    om_client = None
    if args.ingest_om:
        if not OM_TOKEN:
            log.error("Kein OM_TOKEN gesetzt für OpenMetadata Ingestion.")
            log.error("  Export: export OM_TOKEN='eyJ...'")
            sys.exit(1)
        om_client = OMClient(OM_URL, OM_TOKEN)
        log.info("OpenMetadata: %s", OM_URL)
        log.info("Trino-Service: %s", TRINO_SERVICE_FQN)

    # 4. Sync starten
    run_sync(
        cognos_client=cognos_client,
        folder_name=folder_name,
        stats=stats,
        export_manager=export_manager,
        do_export=args.export or args.ingest_om,
        do_ingest=args.ingest_om,
        do_list=args.list,
        types_filter=types_filter,
        om_client=om_client,
    )

    # 5. Zusammenfassung
    log.info("")
    log.info("=" * 70)
    log.info("ZUSAMMENFASSUNG")
    log.info("=" * 70)
    log.info("Gefunden:    %d Module, %d Dashboards", stats.found_modules, stats.found_dashboards)
    log.info("Exportiert:  %d Module, %d Dashboards", stats.exported_modules, stats.exported_dashboards)
    log.info("Übersprungen (unchanged): %d", stats.skipped_unchanged)
    if args.ingest_om:
        log.info("OM erstellt:     %d", stats.om_created)
        log.info("OM aktualisiert: %d", stats.om_updated)
        log.info("OM Lineage:      %d", stats.om_lineage)
        log.info("OM Fehler:       %d", stats.om_errors)
    log.info("Fehler:       %d", stats.errors)
    log.info("=" * 70)


if __name__ == "__main__":
    main()