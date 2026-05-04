#!/usr/bin/env python3
"""
om_glossary_ingest.py – Glossary Ingestion for OpenMetadata

Liest eine JSON-Struktur (glossary_structure.json) und erstellt
das entsprechende Glossar sowie die hierarchischen Begriffe in OpenMetadata.
"""

from __future__ import annotations
import json
import logging
import os
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

log = logging.getLogger("om_glossary")

# ---------------------------------------------------------------------------
# OpenMetadata REST-Client
# ---------------------------------------------------------------------------
class OMClient:
    """Synchroner HTTP-Client für die OpenMetadata REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base = base_url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.token = token

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

# ---------------------------------------------------------------------------
# Glossary Ingester
# ---------------------------------------------------------------------------
class GlossaryIngester:
    def __init__(self, client: OMClient) -> None:
        self.client = client

    def ensure_glossary(self, name: str, description: str) -> str:
        """Erstellt oder aktualisiert das Glossar und gibt die ID zurück."""
        existing = self.client.get(f"v1/glossaries/name/{name}")
        if existing:
            log.info("Glossar '%s' existiert bereits (id=%s)", name, existing["id"])
            return existing["id"]

        payload = {
            "name": name,
            "displayName": name,
            "description": description,
        }
        result = self.client.put("v1/glossaries", payload)
        log.info("Glossar '%s' erstellt (id=%s)", name, result.get("id"))
        return result.get("id")

    def ingest_terms(self, glossary_id: str, terms: list[dict], parent_term_id: str | None = None) -> None:
        """Erstellt rekursiv Glossar-Begriffe."""
        for term_data in terms:
            name = term_data["name"]
            description = term_data.get("description", "")
            tags = term_data.get("tags", [])

            # Suche nach existierendem Term (über Namen und Parent)
            # Hinweis: In OM ist die Suche nach Terms oft über den Namen im Glossar möglich
            # Für Einfachheit nutzen wir hier den PUT-Endpunkt mit dem Namen als Unique Identifier
            # (In einer produktiven Umgebung sollte man die FQN prüfen)
            
            payload = {
                "name": name,
                "displayName": name,
                "description": description,
                "glossary": {"id": glossary_id},
            }
            if parent_term_id:
                payload["parentTerm"] = {"id": parent_term_id}

            try:
                # Hier nutzen wir POST/PUT je nach API Version. 
                # /v1/glossaries/terms ist der Standard-Endpunkt.
                result = self.client.put("v1/glossaries/terms", payload)
                term_id = result.get("id")
                log.info("  ✓ Term '%s' erstellt/aktualisiert (id=%s)", name, term_id)
                
                # Tags hinzufügen (vereinfacht: als Beschreibung oder via separate API falls benötigt)
                if tags:
                    tag_info = ", ".join(tags)
                    # Wir hängen Tags an die Beschreibung an, da das Tagging von Terms 
                    # oft über die Classification API läuft.
                    if tags:
                        new_desc = f"{description}\n\nTags: {tag_info}"
                        self.client.put(f"v1/glossaries/terms/{term_id}", {"description": new_desc})

                # Kinder rekursiv verarbeiten
                children = term_data.get("children", [])
                if children:
                    self.ingest_terms(glossary_id, children, term_id)

            except Exception as e:
                log.error("  ✗ Fehler bei Term '%s': %s", name, e)

def main():
    parser = argparse.ArgumentParser(description="Ingest Glossary Structure to OpenMetadata")
    parser.add_argument("jsonfile", type=Path, help="Pfad zur glossary_structure.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-7s] %(message)s")

    if not args.jsonfile.exists():
        log.error("Datei nicht gefunden: %s", args.jsonfile)
        sys.exit(1)

    with open(args.jsonfile, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not OM_TOKEN:
        log.error("Kein OM_TOKEN gesetzt.")
        sys.exit(1)

    client = OMClient(OM_URL, OM_TOKEN)
    ingester = GlossaryIngester(client)

    log.info("Starte Glossar-Ingestion: %s", data["glossaryName"])
    glossary_id = ingester.ensure_glossary(data["glossaryName"], data["description"])
    ingester.ingest_terms(glossary_id, data["terms"])
    log.info("Glossar-Ingestion abgeschlossen.")

if __name__ == "__main__":
    import argparse
    main()