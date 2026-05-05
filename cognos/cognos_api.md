# Cognos Analytics REST API

**Basis-URL:** `http://192.168.178.149:9300/api/v1`  
**Swagger-Doku:** `http://192.168.178.149:9300/api/api-docs/` (Swagger UI)  
**Swagger-Spec:** `http://192.168.178.149:9300/api/api-docs/swagger.json`

> Wichtig: Der Server spricht nur **HTTP** (kein HTTPS). Tools die automatisch auf HTTPS upgraden (z. B. WebFetch) schlagen fehl. Stattdessen `curl` via Bash verwenden.

---

## Authentifizierung / Session

Der Server ist für **anonymen Zugriff** konfiguriert. Eine Session wird automatisch angelegt, sobald man den Session-Endpunkt aufruft. Die zurückgegebenen Cookies müssen bei allen Folge-Requests mitgeschickt werden.

### Session anlegen

```bash
curl -c /tmp/cognos_cookies.txt \
  "http://192.168.178.149:9300/api/v1/session"
```

**Antwort (Beispiel):**
```json
{
  "isAnonymous": true,
  "session_key": "CAM ...",
  "cafContextId": "CAFW...",
  "url": "/api/v1"
}
```

### XSRF-Token

Der Server gibt einen `XSRF-TOKEN`-Cookie zurück. Dieser muss bei allen schreibenden Requests (und manchen lesenden) als Header mitgeschickt werden:

```
X-XSRF-Token: <Wert aus XSRF-TOKEN Cookie>
```

Den Wert aus der Cookie-Datei lesen:
```bash
grep XSRF-TOKEN /tmp/cognos_cookies.txt | awk '{print $NF}'
```

### Typischer Ablauf für alle API-Calls

```bash
# 1. Session aufbauen (einmalig, Cookies speichern)
curl -c /tmp/cognos_cookies.txt "http://192.168.178.149:9300/api/v1/session"

# 2. XSRF-Token extrahieren
XSRF=$(grep XSRF-TOKEN /tmp/cognos_cookies.txt | awk '{print $NF}')

# 3. API-Call mit Cookies und Token
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/<endpunkt>"
```

---

## Wichtige Endpunkte

### Content-Objekt per ID abrufen (Metadaten)

```bash
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/content/<ID>"
```

Gibt Basisinfos zurück: `id`, `type`, `defaultName`, `modificationTime`, `version`, `owner`.

### Datenmodul-Definition abrufen (vollständig)

```bash
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/modules/<ID>"
```

Gibt die vollständige JSON-Modulspezifikation zurück (Query-Subjects, Items, Relationships, Drill-Groups etc.).

### Modul-Metadaten

```bash
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/modules/<ID>/metadata"
```

### Inhalt eines Ordners (Child-Objekte)

```bash
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/content/<ID>/items"
```

### Alle Content-Objekte (Root)

```bash
curl -b /tmp/cognos_cookies.txt \
  -H "X-XSRF-Token: $XSRF" \
  "http://192.168.178.149:9300/api/v1/content"
```

---

## Objekt-IDs

Cognos-IDs haben das Format `i` + 32 Hex-Zeichen, z. B.:  
`iC2079DD71DCF4B73A8EBDB40578ECDD5`

Die ID eines Objekts findet man in Cognos Analytics über die Eigenschaften des Objekts in der UI, oder über den `/content`-Endpunkt.

---

## Fehlerbehandlung

| HTTP-Status | Bedeutung |
|---|---|
| 200 | OK |
| 401 | Keine Session / Cookies fehlen |
| 403 | "Bereits in allen Namespaces authentifiziert" → Session ist aktiv, nochmaliger Login-Versuch wird abgelehnt (kein echtes Problem) |

---

## Getestete Objekte

| Name | ID | Typ |
|---|---|---|
| Heizung Zeitverlauf | `iC2079DD71DCF4B73A8EBDB40578ECDD5` | module |
