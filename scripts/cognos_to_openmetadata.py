#!/usr/bin/env python3
"""
cognos_to_openmetadata.py – Cognos Analytics → OpenMetadata

Liest Cognos Analytics JSON-Exporte (Datenmodule + Dashboards) und erstellt
die semantische Schicht sowie Visualisierungen in OpenMetadata:

  Datenmodul-Modus (Standard):
  - Dashboard Service (CustomDashboard) für Cognos Analytics
  - Dashboard Data Models pro Query Subject mit Spalten-Metadaten
  - Classification + Tags für Cognos-Usage-Rollen (Identifier/Attribute/Fact)
  - Lineage zu physischen Trino-Tabellen (iceberg.smarthome)
  - Beziehungs-Metadaten (Joins, Kardinalitäten) als Beschreibung

  Dashboard-Modus (--dashboard):
  - Dashboard-Entität mit Tab-Übersicht
  - Chart-Entitäten pro Widget (Name, Typ, referenzierte Spalten)
  - Verknüpfung Dashboard → Data Models (über Datenmodul-Name-Matching)

Idempotent & generisch: Funktioniert nach jeder Änderung am Datenmodul/Dashboard.

Ausführung:
    # Datenmodul ingesten:
    python3 scripts/cognos_to_openmetadata.py /pfad/zu/datamodule.json

    # Dashboard ingesten (setzt voraus, dass Datenmodul bereits ingestiert wurde):
    python3 scripts/cognos_to_openmetadata.py --dashboard /pfad/zu/dashboard.json

Umgebungsvariablen:
    OM_URL          OpenMetadata API URL     (default: http://localhost:8585/api)
    OM_TOKEN        Bot-Token                (default: aus om_dbt_ingestion.yaml)
    OM_TRINO_SVC    Trino Service-Name in OM (default: lakehouse_trino)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
OM_URL = os.environ.get("OM_URL", "http://localhost:8585/api")
OM_TOKEN = os.environ.get("OM_TOKEN", "")
TRINO_SERVICE_FQN = os.environ.get("OM_TRINO_SVC", "lakehouse_trino")
COGNOS_SERVICE_NAME = "cognos_analytics"

log = logging.getLogger("cognos2om")

# ---------------------------------------------------------------------------
# Cognos → OpenMetadata Datentyp-Mapping
# ---------------------------------------------------------------------------
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

# Cognos usage → Classification-Tag
_COGNOS_USAGE_MAP: dict[str, str] = {
    "identifier": "Identifier",
    "attribute":  "Attribute",
    "fact":       "Measure",
}


def _map_datatype(cognos_type: str) -> tuple[str, str]:
    """Gibt (om_dataType, dataTypeDisplay) für einen Cognos-Datentyp zurück."""
    raw = cognos_type.strip().upper()
    # Typ-Parameter extrahieren: DECIMAL(38,0) → base=DECIMAL, display=DECIMAL(38,0)
    base = re.split(r"[(\s]", raw)[0]
    om_type = _COGNOS_DATATYPE_MAP.get(base, "STRING")
    return om_type, cognos_type


# ---------------------------------------------------------------------------
# OpenMetadata REST-Client (minimal, ohne externe Abhängigkeiten)
# ---------------------------------------------------------------------------
class OMClient:
    """Synchroner HTTP-Client für die OpenMetadata REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        # Trailing Slash/api normalisieren
        self.base = base_url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.token = token

    # -- Low-Level --------------------------------------------------------

    def _request(
        self, method: str, path: str, data: Any = None, *, content_type: str = "application/json"
    ) -> dict | list | None:
        url = f"{self.base}/{path.lstrip('/')}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace") if exc.fp else ""
            if exc.code == 404:
                return None
            log.error("HTTP %s %s → %d: %s", method, url, exc.code, body_text[:500])
            raise

    def get(self, path: str) -> dict | None:
        return self._request("GET", path)

    def put(self, path: str, data: Any) -> dict:
        return self._request("PUT", path, data)

    def post(self, path: str, data: Any) -> dict:
        return self._request("POST", path, data)

    def patch(self, path: str, data: Any) -> dict:
        return self._request("PATCH", path, data, content_type="application/json-patch+json")


# ---------------------------------------------------------------------------
# Cognos Data Module Parser
# ---------------------------------------------------------------------------
class CognosDataModule:
    """Parsed ein Cognos Data Module JSON in strukturierte Objekte."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.identifier: str = raw.get("identifier", "unknown")
        self.label: str = raw.get("label", self.identifier)
        # Sanitized label als OM-kompatibler Name (Leerzeichen → Unterstriche)
        self.name: str = re.sub(r"[^a-zA-Z0-9_]+", "_", self.label).strip("_")
        self.last_modified: str = raw.get("lastModified", "")
        self.expression_locale: str = raw.get("expressionLocale", "")

        # Datenquelle
        use_spec = raw.get("useSpec", [{}])[0] if raw.get("useSpec") else {}
        ds_override = use_spec.get("dataSourceOverride", {})
        self.catalog: str = ds_override.get("catalog", "iceberg")
        self.schema: str = ds_override.get("schema", "smarthome")
        self.datasource_name: str = ds_override.get("cmDataSource", "Trino Lakehouse")

        # Query Subjects
        self.query_subjects: list[dict] = raw.get("querySubject", [])
        # Relationships
        self.relationships: list[dict] = raw.get("relationship", [])
        # Drill Groups
        self.drill_groups: list[dict] = raw.get("drillGroup", [])
        # Custom Sorts
        self.custom_sorts: list[dict] = raw.get("customSort", [])

    def get_physical_table_name(self, qs: dict) -> str | None:
        """Extrahiert den physischen Tabellennamen aus dem ref-Feld eines Query Subjects."""
        refs = qs.get("ref", [])
        if refs:
            # Format: "M1.dim_tag" → "dim_tag"
            return refs[0].split(".", 1)[-1]
        # SQL Query Subjects haben kein ref
        return None

    def is_sql_query_subject(self, qs: dict) -> bool:
        return qs.get("classifier") == "sqlQuerySubject"

    def get_sql_text(self, qs: dict) -> str | None:
        sql_query = qs.get("sqlQuery")
        return sql_query.get("sqlText") if sql_query else None

    def build_relationship_docs(self) -> dict[str, list[str]]:
        """Erstellt pro Query Subject eine Liste von Beziehungs-Beschreibungen."""
        docs: dict[str, list[str]] = {}
        for rel in self.relationships:
            left = rel.get("left", {})
            right = rel.get("right", {})
            left_ref = left.get("ref", "?")
            right_ref = right.get("ref", "?")
            left_card = f"{left.get('mincard', '?')}:{left.get('maxcard', '?')}"
            right_card = f"{right.get('mincard', '?')}:{right.get('maxcard', '?')}"
            links = rel.get("link", [])
            join_cols = ", ".join(
                f"{lk.get('leftRef', '?')} = {lk.get('rightRef', '?')}" for lk in links
            )
            desc = (
                f"**{left_ref}** ({left_card}) ↔ **{right_ref}** ({right_card}) "
                f"ON {join_cols}"
            )
            docs.setdefault(left_ref, []).append(desc)
            docs.setdefault(right_ref, []).append(desc)
        return docs

    def build_drill_group_docs(self) -> str:
        """Erstellt Markdown-Beschreibung der Drill Groups."""
        if not self.drill_groups:
            return ""
        lines = ["### Drill-Hierarchien\n"]
        for dg in self.drill_groups:
            label = dg.get("label", dg.get("identifier", "?"))
            segments = dg.get("segment", [])
            seg_labels = [s.get("label", s.get("identifier", "?")) for s in segments]
            lines.append(f"- **{label}**: {' → '.join(seg_labels)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cognos visId → OM ChartType Mapping
# ---------------------------------------------------------------------------
_COGNOS_VIS_MAP: dict[str, str] = {
    "com.ibm.vis.rave2bundlecolumn": "Bar",
    "com.ibm.vis.rave2bundlebar": "Bar",
    "com.ibm.vis.rave2bundlestackedcolumn": "Bar",
    "com.ibm.vis.rave2line": "Line",
    "com.ibm.vis.rave2bundlecomposite": "Other",
    "com.ibm.vis.rave2bundlearea": "Area",
    "com.ibm.vis.rave2bundlepie": "Pie",
    "com.ibm.vis.rave2bundlescatter": "Scatter",
    "com.ibm.vis.rave2bundletreemap": "Other",
    "com.ibm.vis.rave2bundleheatmap": "Other",
    "com.ibm.vis.rave2bundletable": "Table",
    "com.ibm.vis.rave2crosstab": "Table",
    "summary": "Text",
}


# ---------------------------------------------------------------------------
# Cognos Dashboard Parser
# ---------------------------------------------------------------------------
class CognosDashboard:
    """Parsed ein Cognos Dashboard JSON in strukturierte Objekte."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.dashboard_name: str = raw.get("name", "Unbekanntes Dashboard")
        # Sanitized name als OM-kompatibler Name
        self.name: str = re.sub(r"[^a-zA-Z0-9_]+", "_", self.dashboard_name).strip("_")
        self.version: int = raw.get("version", 0)

        # Datenquellen (referenzierte Datenmodule)
        sources_section = raw.get("dataSources", {})
        self.sources: list[dict] = sources_section.get("sources", [])

        # Tabs und Widgets extrahieren
        self.tabs: list[dict] = []
        self.widgets: list[dict] = []
        self._extract_layout(raw.get("layout", {}))

        # Globale Spaltenliste aus MetadataLoader
        features = raw.get("features", {})
        ml = features.get("MetadataLoader", {})
        self.all_column_ids: list[str] = []
        for _src_id, col_list in ml.get("metadataSubsetIds", {}).items():
            self.all_column_ids.extend(col_list)

    def _extract_layout(self, layout: dict) -> None:
        """Traversiert layout.items[] rekursiv und sammelt Tabs + Widgets."""
        items = layout.get("items", [])
        for item in items:
            item_type = item.get("type", "")
            if item_type == "container":
                # Tab-Ebene: container mit title
                title_table = item.get("title", {}).get("translationTable", {})
                tab_title = title_table.get("Default", "")
                tab_widgets: list[dict] = []
                self._collect_widgets(item, tab_widgets)
                self.tabs.append({"title": tab_title, "id": item.get("id", ""), "widgets": tab_widgets})
                self.widgets.extend(tab_widgets)
            elif item_type == "widget":
                # Widget auf Top-Level (selten, aber möglich)
                w = self._parse_widget(item)
                if w:
                    self.widgets.append(w)

    def _collect_widgets(self, node: dict, result: list[dict]) -> None:
        """Sammelt alle Widgets rekursiv aus verschachtelten items[]."""
        for child in node.get("items", []):
            if child.get("type") == "widget":
                w = self._parse_widget(child)
                if w:
                    result.append(w)
            elif "items" in child:
                self._collect_widgets(child, result)

    def _parse_widget(self, node: dict) -> dict | None:
        """Extrahiert relevante Metadaten aus einem Widget-Knoten."""
        features = node.get("features", {})
        model = features.get("Models_internal", {})
        if not model:
            return None

        vis_id = model.get("visId", "")
        # Name aus translationTable
        name_table = model.get("name", {}).get("translationTable", {})
        widget_name = name_table.get("Default", "")

        # Referenzierte Spalten aus allen dataViews sammeln
        columns: list[str] = []
        data = model.get("data", {})
        for dv in data.get("dataViews", []):
            for di in dv.get("dataItems", []):
                item_id = di.get("itemId", "")
                # _multiMeasuresSeries ist ein synthetisches Cognos-Item, kein echtes Feld
                if item_id and not item_id.startswith("_"):
                    columns.append(item_id)

        # Slot-Mapping: welche Rolle hat jede Spalte im Chart?
        slot_info: dict[str, list[str]] = {}
        id_to_item: dict[str, str] = {}
        for dv in data.get("dataViews", []):
            for di in dv.get("dataItems", []):
                di_id = di.get("id", "")
                item_id = di.get("itemId", "")
                if di_id and item_id and not item_id.startswith("_"):
                    id_to_item[di_id] = item_id

        for slot in model.get("slotmapping", {}).get("slots", []):
            slot_name = slot.get("name", "")
            for di_ref in slot.get("dataItems", []):
                if di_ref in id_to_item:
                    slot_info.setdefault(slot_name, []).append(id_to_item[di_ref])

        return {
            "id": node.get("id", ""),
            "name": widget_name,
            "visId": vis_id,
            "chartType": _COGNOS_VIS_MAP.get(vis_id, "Other"),
            "columns": columns,
            "slots": slot_info,
        }

    def get_data_module_names(self) -> list[str]:
        """Gibt die Namen der referenzierten Datenmodule zurück."""
        return [s.get("name", "") for s in self.sources if s.get("name")]

    def get_referenced_query_subjects(self) -> set[str]:
        """Ermittelt alle referenzierten Query Subjects aus den Widget-Spalten."""
        qs_names: set[str] = set()
        for w in self.widgets:
            for col in w.get("columns", []):
                # Format: "dim_tag.year_" → Query Subject = "dim_tag"
                if "." in col:
                    qs_names.add(col.split(".")[0])
        return qs_names


# ---------------------------------------------------------------------------
# OpenMetadata Ingestion
# ---------------------------------------------------------------------------
class CognosOMIngester:
    """Ingestet ein geparstes Cognos Data Module nach OpenMetadata."""

    CLASSIFICATION_NAME = "CognosAnalytics"
    CLASSIFICATION_TAGS = {
        "Identifier": "Cognos Usage: Identifier – Eindeutiges Identifikationsmerkmal (Dimension-Key)",
        "Attribute":  "Cognos Usage: Attribute – Beschreibendes Merkmal (Dimension)",
        "Measure":    "Cognos Usage: Measure/Fact – Messgröße (Kennzahl)",
    }

    def __init__(self, client: OMClient, trino_service_fqn: str) -> None:
        self.client = client
        self.trino_svc = trino_service_fqn
        self._created = 0
        self._updated = 0
        self._lineage = 0
        self._errors = 0

    # -- Setup: Service & Tags --------------------------------------------

    def ensure_dashboard_service(self) -> None:
        """Erstellt den Cognos Dashboard Service falls nicht vorhanden."""
        existing = self.client.get(f"v1/services/dashboardServices/name/{COGNOS_SERVICE_NAME}")
        if existing:
            log.info("Dashboard-Service '%s' existiert bereits (id=%s)", COGNOS_SERVICE_NAME, existing["id"])
            return

        payload = {
            "name": COGNOS_SERVICE_NAME,
            "displayName": "Cognos Analytics",
            "description": (
                "IBM Cognos Analytics – Semantische Schicht (Datenmodule). "
                "Automatisch ingestiert aus Cognos Data Module JSON-Exporten."
            ),
            "serviceType": "CustomDashboard",
            "connection": {
                "config": {
                    "type": "CustomDashboard",
                    "sourcePythonClass": "cognos_to_openmetadata",
                    "dashboardUrl": "https://cognos.local",
                }
            },
        }
        result = self.client.put("v1/services/dashboardServices", payload)
        log.info("Dashboard-Service '%s' erstellt (id=%s)", COGNOS_SERVICE_NAME, result.get("id"))

    def ensure_classification_and_tags(self) -> None:
        """Erstellt Classification 'CognosAnalytics' mit Usage-Tags."""
        cls_name = self.CLASSIFICATION_NAME
        existing = self.client.get(f"v1/classifications/name/{cls_name}")
        if not existing:
            self.client.put("v1/classifications", {
                "name": cls_name,
                "displayName": "Cognos Analytics",
                "description": "Tags für Cognos Analytics Datenmodul-Metadaten (Usage-Rollen).",
            })
            log.info("Classification '%s' erstellt", cls_name)

        for tag_name, tag_desc in self.CLASSIFICATION_TAGS.items():
            tag_existing = self.client.get(f"v1/tags/name/{cls_name}.{tag_name}")
            if not tag_existing:
                self.client.post("v1/tags", {
                    "name": tag_name,
                    "displayName": tag_name,
                    "description": tag_desc,
                    "classification": cls_name,
                })
                log.info("  Tag '%s.%s' erstellt", cls_name, tag_name)

    # -- Data Model Erstellung --------------------------------------------

    def _build_columns(self, items: list[dict]) -> list[dict]:
        """Wandelt Cognos queryItems in OM-Column-Definitionen um."""
        columns = []
        for item_wrapper in items:
            qi = item_wrapper.get("queryItem")
            if not qi:
                continue
            om_type, display_type = _map_datatype(qi.get("datatype", "VARCHAR"))
            usage = qi.get("usage", "")
            tag_name = _COGNOS_USAGE_MAP.get(usage)

            # Beschreibung aus Cognos-Metadaten zusammenbauen
            desc_parts = []
            if qi.get("label") and qi.get("label") != qi.get("identifier"):
                desc_parts.append(f"Label: {qi['label']}")
            desc_parts.append(f"Usage: {usage}")
            agg = qi.get("regularAggregate", "")
            if agg:
                desc_parts.append(f"Aggregation: {agg}")
            taxonomy = qi.get("taxonomy", [])
            for tax in taxonomy:
                desc_parts.append(f"Taxonomy: {tax.get('domain','')}/{tax.get('class','')}/{tax.get('family','')}")

            col: dict[str, Any] = {
                "name": qi.get("identifier", qi.get("expression", "unknown")),
                "displayName": qi.get("label", qi.get("identifier", "")),
                "dataType": om_type,
                "dataTypeDisplay": display_type,
                "description": " | ".join(desc_parts),
            }

            if tag_name:
                col["tags"] = [{
                    "tagFQN": f"{self.CLASSIFICATION_NAME}.{tag_name}",
                    "source": "Classification",
                    "labelType": "Automated",
                    "state": "Confirmed",
                }]

            columns.append(col)
        return columns

    def _build_description(
        self, qs: dict, module: CognosDataModule, rel_docs: dict[str, list[str]]
    ) -> str:
        """Erstellt eine Markdown-Beschreibung für ein Data Model."""
        qs_id = qs.get("identifier", "")
        qs_label = qs.get("label", qs_id)
        lines = [
            f"**Cognos Data Module**: {module.label}",
            f"**Datenquelle**: {module.datasource_name} → `{module.catalog}.{module.schema}`",
        ]

        if qs.get("description"):
            lines.append(f"\n{qs['description']}")

        if module.is_sql_query_subject(qs):
            sql = module.get_sql_text(qs)
            if sql:
                lines.append(f"\n**SQL**:\n```sql\n{sql}\n```")

        # Beziehungen
        rels = rel_docs.get(qs_id, [])
        if rels:
            lines.append("\n### Beziehungen")
            for r in rels:
                lines.append(f"- {r}")

        return "\n".join(lines)

    def ingest_data_model(
        self, qs: dict, module: CognosDataModule, rel_docs: dict[str, list[str]]
    ) -> str | None:
        """Erstellt/aktualisiert ein Dashboard Data Model für ein Query Subject."""
        qs_id = qs.get("identifier", "unknown")
        qs_label = qs.get("label", qs_id)

        # Eindeutiger Name: <module_name>.<query_subject_id>
        model_name = f"{module.name}__{qs_id}"
        columns = self._build_columns(qs.get("item", []))
        description = self._build_description(qs, module, rel_docs)

        payload = {
            "name": model_name,
            "displayName": f"{qs_label}  ({module.label})",
            "description": description,
            "service": COGNOS_SERVICE_NAME,
            "dataModelType": "Other",
            "serviceType": "CustomDashboard",
            "columns": columns,
        }

        try:
            result = self.client.put("v1/dashboard/datamodels", payload)
            model_id = result.get("id")
            fqn = result.get("fullyQualifiedName", "")
            if result.get("version", 0.0) <= 0.1:
                self._created += 1
                log.info("  ✓ Data Model erstellt: %s (id=%s)", fqn, model_id)
            else:
                self._updated += 1
                log.info("  ↻ Data Model aktualisiert: %s (v%.1f)", fqn, result.get("version", 0))
            return model_id
        except urllib.error.HTTPError as exc:
            self._errors += 1
            log.error("  ✗ Fehler bei Data Model '%s': %s", model_name, exc)
            return None

    # -- Lineage -----------------------------------------------------------

    def create_lineage_to_trino(
        self, data_model_id: str, table_name: str, module: CognosDataModule
    ) -> None:
        """Erstellt Lineage: Trino-Tabelle → Dashboard Data Model."""
        table_fqn = f"{self.trino_svc}.{module.catalog}.{module.schema}.{table_name}"
        # Trino-Tabelle in OM nachschlagen
        table_entity = self.client.get(f"v1/tables/name/{table_fqn}")
        if not table_entity:
            log.warning("  ⚠ Trino-Tabelle nicht gefunden: %s (Lineage übersprungen)", table_fqn)
            return

        table_id = table_entity["id"]
        lineage_payload = {
            "edge": {
                "fromEntity": {"id": table_id, "type": "table"},
                "toEntity": {"id": data_model_id, "type": "dashboardDataModel"},
            },
        }
        try:
            self.client.put("v1/lineage", lineage_payload)
            self._lineage += 1
            log.info("  → Lineage: %s → DataModel", table_fqn)
        except urllib.error.HTTPError as exc:
            log.warning("  ⚠ Lineage-Fehler für %s: %s", table_fqn, exc)

    # -- Haupt-Ingestion ---------------------------------------------------

    def ingest(self, module: CognosDataModule) -> None:
        """Führt die vollständige Ingestion eines Cognos Data Modules durch."""
        log.info("=" * 70)
        log.info("Cognos Data Module: %s", module.label)
        log.info("  Identifier:     %s", module.identifier)
        log.info("  Datenquelle:    %s → %s.%s", module.datasource_name, module.catalog, module.schema)
        log.info("  Query Subjects: %d", len(module.query_subjects))
        log.info("  Relationships:  %d", len(module.relationships))
        log.info("=" * 70)

        # 1. Infrastruktur sicherstellen
        log.info("Schritt 1: Dashboard-Service & Tags sicherstellen...")
        self.ensure_dashboard_service()
        self.ensure_classification_and_tags()

        # 2. Beziehungs-Dokumentation vorab aufbauen
        rel_docs = module.build_relationship_docs()

        # 3. Data Models erstellen
        log.info("Schritt 2: Dashboard Data Models erstellen/aktualisieren...")
        for qs in module.query_subjects:
            qs_id = qs.get("identifier", "unknown")
            log.info("  Verarbeite: %s", qs_id)

            model_id = self.ingest_data_model(qs, module, rel_docs)
            if not model_id:
                continue

            # 4. Lineage zu physischer Trino-Tabelle
            table_name = module.get_physical_table_name(qs)
            if table_name:
                self.create_lineage_to_trino(model_id, table_name, module)
            elif module.is_sql_query_subject(qs):
                log.info("  ℹ SQL Query Subject '%s' – keine direkte Tabellen-Lineage", qs_id)

        # 5. Zusammenfassung
        log.info("-" * 70)
        log.info("Zusammenfassung:")
        log.info("  Erstellt:      %d Data Models", self._created)
        log.info("  Aktualisiert:  %d Data Models", self._updated)
        log.info("  Lineage-Edges: %d", self._lineage)
        log.info("  Fehler:        %d", self._errors)
        log.info("-" * 70)

        # 6. Drill Groups als Modul-Level Beschreibung
        drill_docs = module.build_drill_group_docs()
        if drill_docs:
            log.info("Drill Groups dokumentiert:\n%s", drill_docs)

    # -- Dashboard-Ingestion -----------------------------------------------

    def ingest_dashboard(self, dashboard: CognosDashboard) -> None:
        """Führt die vollständige Ingestion eines Cognos Dashboards durch."""
        log.info("=" * 70)
        log.info("Cognos Dashboard: %s", dashboard.dashboard_name)
        log.info("  Tabs:    %d", len(dashboard.tabs))
        log.info("  Widgets: %d", len(dashboard.widgets))
        log.info("  Datenmodule: %s", ", ".join(dashboard.get_data_module_names()))
        log.info("  Referenzierte Query Subjects: %s", ", ".join(sorted(dashboard.get_referenced_query_subjects())))
        log.info("  Spalten gesamt: %d", len(dashboard.all_column_ids))
        log.info("=" * 70)

        # 1. Service sicherstellen
        log.info("Schritt 1: Dashboard-Service sicherstellen...")
        self.ensure_dashboard_service()

        # 2. Charts erstellen (pro Widget)
        log.info("Schritt 2: Charts erstellen...")
        chart_fqns: list[str] = []
        for widget in dashboard.widgets:
            chart_fqn = self._ingest_chart(widget, dashboard)
            if chart_fqn:
                chart_fqns.append(chart_fqn)

        # 3. Data Model FQNs ermitteln (für dataModels-Referenz)
        data_model_fqns: list[str] = []
        for dm_name in dashboard.get_data_module_names():
            sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", dm_name).strip("_")
            for qs_name in sorted(dashboard.get_referenced_query_subjects()):
                model_fqn = f"{COGNOS_SERVICE_NAME}.{sanitized}__{qs_name}"
                data_model_fqns.append(model_fqn)

        # 4. Dashboard erstellen
        log.info("Schritt 3: Dashboard erstellen...")
        description = self._build_dashboard_description(dashboard)
        payload: dict[str, Any] = {
            "name": dashboard.name,
            "displayName": dashboard.dashboard_name,
            "description": description,
            "service": COGNOS_SERVICE_NAME,
            "serviceType": "CustomDashboard",
            "charts": [{"fullyQualifiedName": fqn} for fqn in chart_fqns],
            "dataModels": [{"fullyQualifiedName": fqn} for fqn in data_model_fqns],
        }

        try:
            result = self.client.put("v1/dashboards", payload)
            dash_id = result.get("id")
            fqn = result.get("fullyQualifiedName", "")
            if result.get("version", 0.0) <= 0.1:
                self._created += 1
                log.info("  ✓ Dashboard erstellt: %s (id=%s)", fqn, dash_id)
            else:
                self._updated += 1
                log.info("  ↻ Dashboard aktualisiert: %s (v%.1f)", fqn, result.get("version", 0))
        except urllib.error.HTTPError as exc:
            self._errors += 1
            log.error("  ✗ Fehler bei Dashboard '%s': %s", dashboard.name, exc)

        # 5. Zusammenfassung
        log.info("-" * 70)
        log.info("Zusammenfassung:")
        log.info("  Charts erstellt/aktualisiert: %d", len(chart_fqns))
        log.info("  Data Models verknüpft:        %d", len(data_model_fqns))
        log.info("  Fehler:                       %d", self._errors)
        log.info("-" * 70)

    def _ingest_chart(self, widget: dict, dashboard: CognosDashboard) -> str | None:
        """Erstellt/aktualisiert einen Chart für ein Dashboard-Widget."""
        widget_id = widget.get("id", "unknown")
        widget_name = widget.get("name", "")
        # Sanitized Name für OM (stabil über Widget-ID)
        chart_name = f"{dashboard.name}__{re.sub(r'[^a-zA-Z0-9_]+', '_', widget_name).strip('_') or widget_id}"

        chart_type = widget.get("chartType", "Other")
        columns = widget.get("columns", [])
        slots = widget.get("slots", {})
        vis_id = widget.get("visId", "")

        # Beschreibung mit Spalten-Details
        desc_lines = [
            f"**Dashboard**: {dashboard.dashboard_name}",
            f"**Cognos visId**: `{vis_id}`",
        ]
        if columns:
            desc_lines.append(f"\n**Referenzierte Spalten**: {', '.join(f'`{c}`' for c in columns)}")
        if slots:
            desc_lines.append("\n**Slot-Mapping**:")
            for slot_name, slot_cols in slots.items():
                desc_lines.append(f"- **{slot_name}**: {', '.join(f'`{c}`' for c in slot_cols)}")

        payload: dict[str, Any] = {
            "name": chart_name,
            "displayName": widget_name or widget_id,
            "description": "\n".join(desc_lines),
            "service": COGNOS_SERVICE_NAME,
            "serviceType": "CustomDashboard",
            "chartType": chart_type,
        }

        try:
            result = self.client.put("v1/charts", payload)
            fqn = result.get("fullyQualifiedName", "")
            if result.get("version", 0.0) <= 0.1:
                log.info("  ✓ Chart erstellt: %s (%s)", fqn, chart_type)
            else:
                log.info("  ↻ Chart aktualisiert: %s (%s)", fqn, chart_type)
            return fqn
        except urllib.error.HTTPError as exc:
            self._errors += 1
            log.error("  ✗ Fehler bei Chart '%s': %s", chart_name, exc)
            return None

    def _build_dashboard_description(self, dashboard: CognosDashboard) -> str:
        """Erstellt eine Markdown-Beschreibung für das Dashboard."""
        lines = [
            f"**Cognos Dashboard**: {dashboard.dashboard_name}",
            f"**Datenmodule**: {', '.join(dashboard.get_data_module_names())}",
        ]

        # Tab-Übersicht
        if dashboard.tabs:
            lines.append("\n### Tabs")
            for tab in dashboard.tabs:
                tab_title = tab.get("title", "?")
                tab_widgets = tab.get("widgets", [])
                widget_names = [w.get("name", "?") for w in tab_widgets]
                lines.append(f"- **{tab_title}** ({len(tab_widgets)} Widgets): {', '.join(widget_names)}")

        # Globale Spaltenliste
        if dashboard.all_column_ids:
            lines.append(f"\n### Referenzierte Spalten ({len(dashboard.all_column_ids)})")
            # Nach Query Subject gruppieren
            by_qs: dict[str, list[str]] = {}
            for col_id in dashboard.all_column_ids:
                if "." in col_id:
                    qs, col = col_id.split(".", 1)
                    by_qs.setdefault(qs, []).append(col)
            for qs in sorted(by_qs):
                lines.append(f"- **{qs}**: {', '.join(sorted(by_qs[qs]))}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cognos Analytics → OpenMetadata Ingestion (Datenmodule + Dashboards)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
    # Datenmodul ingesten:
    python3 scripts/cognos_to_openmetadata.py datamodule.json

    # Dashboard ingesten (Datenmodul muss vorher ingestiert worden sein):
    python3 scripts/cognos_to_openmetadata.py --dashboard dashboard.json

    # Dry-Run für Dashboard:
    python3 scripts/cognos_to_openmetadata.py --dashboard --dry-run -v dashboard.json

Umgebungsvariablen:
    OM_URL          OpenMetadata API URL     (default: http://localhost:8585/api)
    OM_TOKEN        Bot-Token für Auth       (required)
    OM_TRINO_SVC    Trino Service-Name       (default: lakehouse_trino)
        """,
    )
    parser.add_argument(
        "jsonfile",
        type=Path,
        metavar="JSON_FILE",
        help="Pfad zur Cognos JSON-Datei (Datenmodul oder Dashboard)",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Dashboard-Modus: JSON als Cognos Dashboard interpretieren (statt Datenmodul)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur parsen und validieren, keine API-Calls",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Debug-Logging aktivieren",
    )
    args = parser.parse_args()

    # Logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # JSON lesen
    json_path: Path = args.jsonfile
    if not json_path.exists():
        log.error("Datei nicht gefunden: %s", json_path)
        sys.exit(1)

    raw = json.loads(json_path.read_text(encoding="utf-8"))

    # --- Dashboard-Modus ---
    if args.dashboard:
        log.info("Lese Cognos Dashboard: %s", json_path)
        dashboard = CognosDashboard(raw)

        if args.dry_run:
            log.info("=== DRY RUN (Dashboard) ===")
            log.info("Dashboard:      %s", dashboard.dashboard_name)
            log.info("OM-Name:        %s", dashboard.name)
            log.info("Datenmodule:    %s", ", ".join(dashboard.get_data_module_names()))
            log.info("Tabs:           %d", len(dashboard.tabs))
            log.info("Widgets:        %d", len(dashboard.widgets))
            log.info("Spalten gesamt: %d", len(dashboard.all_column_ids))
            log.info("Query Subjects: %s", ", ".join(sorted(dashboard.get_referenced_query_subjects())))
            log.info("")
            for tab in dashboard.tabs:
                tab_title = tab.get("title", "?")
                tab_widgets = tab.get("widgets", [])
                log.info("  Tab: %s (%d Widgets)", tab_title, len(tab_widgets))
                for w in tab_widgets:
                    chart_type = w.get("chartType", "?")
                    cols = w.get("columns", [])
                    log.info("    - %-50s  %-6s  %d Spalten", w.get("name", "?")[:50], chart_type, len(cols))
                    if args.verbose:
                        for slot_name, slot_cols in w.get("slots", {}).items():
                            log.debug("        %-14s → %s", slot_name, ", ".join(slot_cols))
            log.info("")
            log.info("Globale Spaltenliste:")
            for col_id in dashboard.all_column_ids:
                log.info("  - %s", col_id)
            sys.exit(0)

        # Token prüfen
        token = OM_TOKEN
        if not token:
            log.error(
                "Kein OM_TOKEN gesetzt. Bot-Token benötigt.\n"
                "  Export: export OM_TOKEN='eyJ...'\n"
                "  Oder Token aus OM UI: Settings → Bots → ingestion-bot"
            )
            sys.exit(1)

        client = OMClient(OM_URL, token)
        ingester = CognosOMIngester(client, TRINO_SERVICE_FQN)
        ingester.ingest_dashboard(dashboard)
        sys.exit(0)

    # --- Datenmodul-Modus (Standard) ---
    log.info("Lese Cognos Data Module: %s", json_path)
    module = CognosDataModule(raw)

    if args.dry_run:
        log.info("=== DRY RUN (Datenmodul) ===")
        log.info("Modul:          %s (%s)", module.label, module.identifier)
        log.info("OM-Name:        %s", module.name)
        log.info("Datenquelle:    %s.%s", module.catalog, module.schema)
        log.info("Query Subjects: %d", len(module.query_subjects))
        for qs in module.query_subjects:
            qs_id = qs.get("identifier", "?")
            items = qs.get("item", [])
            is_sql = module.is_sql_query_subject(qs)
            log.info("  - %-30s  %2d Spalten  %s", qs_id, len(items), "(SQL)" if is_sql else "")
            if args.verbose:
                for item_wrapper in items:
                    qi = item_wrapper.get("queryItem", {})
                    tag = _COGNOS_USAGE_MAP.get(qi.get("usage", ""), "?")
                    om_type, _ = _map_datatype(qi.get("datatype", "?"))
                    log.debug(
                        "      %-28s  %-12s  %-12s  agg=%s",
                        qi.get("identifier", "?"), om_type, tag, qi.get("regularAggregate", "-"),
                    )
        log.info("Relationships:  %d", len(module.relationships))
        log.info("Drill Groups:   %d", len(module.drill_groups))
        log.info("Custom Sorts:   %d", len(module.custom_sorts))
        sys.exit(0)

    # Token prüfen
    token = OM_TOKEN
    if not token:
        log.error(
            "Kein OM_TOKEN gesetzt. Bot-Token benötigt.\n"
            "  Export: export OM_TOKEN='eyJ...'\n"
            "  Oder Token aus OM UI: Settings → Bots → ingestion-bot"
        )
        sys.exit(1)

    # Ingestion starten
    client = OMClient(OM_URL, token)
    ingester = CognosOMIngester(client, TRINO_SERVICE_FQN)
    ingester.ingest(module)


if __name__ == "__main__":
    main()
