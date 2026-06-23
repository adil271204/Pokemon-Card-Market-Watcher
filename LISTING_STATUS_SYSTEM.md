# Listing Status System

## Überblick

Das Listing Status System ermöglicht es dir, Listings schnell zu bewerten und zu verwalten, statt sie nur zu sehen oder auszublenden.

## Features (MVP)

### 1. Status pro Listing

Erlaubte Status:
- `new` – Neu entdecktes Listing (Standard)
- `interesting` – Interessantes Listing
- `watching` – Wird beobachtet
- `ignored` – Ignoriert (bleibt sichtbar, aber gekennzeichnet)
- `purchased` – Gekauft
- `too_expensive` – Zu teuer
- `wrong_card` – Falsche Karte
- `bad_condition` – Schlechter Zustand
- `shipping_too_high` – Versand zu hoch
- `not_relevant` – Nicht relevant

### 2. Deal Inbox

**Route:** `GET /deal-inbox`

Zentrale Arbeitsansicht für:
- KPI-Karten oben (Neue, Interessant, Beobachten, Gekauft 30d, Auktionen 24h, Deutschland)
- Filter nach Status, Land, Preis, Zeitraum, Watchlist, Titel
- Filter-Presets für schnelle Nutzung
- Tabelle mit Status-Badges, Listing-Typ, Preis, etc.

### 3. Status-APIs

Alle sind HTTP POST mit Redirect zurück:

```
POST /listings/{listing_id}/status
  - status: str (required)
  - reason: str (optional)
  - note: str (optional)
  - return_url: str (optional, defaults to /listings)

POST /listings/bulk-status
  - listing_ids: list[int]
  - status: str (required)
  - reason: str (optional)
  - return_url: str

POST /listings/{listing_id}/note
  - note: str (optional)
  - return_url: str

POST /listings/{listing_id}/restore
  - Stellt gelöschtes Listing wieder her (deleted_at = NULL)
```

### 4. Status-Badges

Sichtbar als Farb-Badges in Tabellen:
- Neu: grau
- Interessant: grün
- Beobachten: blau
- Ignoriert: grau
- Gekauft: dunkelgrün
- Zu teuer: orange
- Falsche Karte: rot
- etc.

### 5. Zeitstempel

Bei Status-Änderung werden automatisch gesetzt:
- `updated_at` – Immer
- `reviewed_at` – Wenn Status != 'new'
- `purchased_at` – Wenn Status == 'purchased'

### 6. Soft Delete vs. Ignored

**Ignorieren** (listing_status = 'ignored'):
- Listing bleibt sichtbar und filterbar
- Wird mit "Ignoriert"-Badge gekennzeichnet
- Kann später wieder aktiviert werden

**Ausblenden** (deleted_at = now()):
- Listing versteckt standardmäßig
- Nur sichtbar mit Checkbox "Ausgeblendete anzeigen"
- Soft Delete bleibt erhalten

## Datenbank

### Neue SeenListing-Felder

```sql
listing_status TEXT DEFAULT 'new'
status_reason TEXT NULL
user_note TEXT NULL
reviewed_at TIMESTAMP NULL
purchased_at TIMESTAMP NULL
updated_at TIMESTAMP NULL
```

Migration: `scripts/migrate_listing_status_fields.py`

## Roadmap (Phase 3+)

### Phase 3 – Schnellaktionen überall

- [ ] Schnellaktions-Buttons in allen Listing-Tabellen
  - Primär sichtbar: eBay, Interessant, Beobachten, Ignorieren
  - Dropdown "Mehr" für: Zu teuer, Falsche Karte, Versand zu hoch, Gekauft, Ausblenden
- [ ] Status-Spalte in Listings + Overview
- [ ] Notiz-Button für schnelle Eingabe
- [ ] Notiz-Anzeige im Hover/Tooltip

### Phase 4 – Analytics

- [ ] Listings nach Status visualisieren
- [ ] Gekaufte Listings pro Monat
- [ ] Interessante Listings pro Watchlist
- [ ] Ignore-Gründe Statistik

### Phase 5 – Smart Features

- [ ] Automatische Status-Vorschläge basierend auf Preis/Zustand
- [ ] Bulk-Aktionen mit Checkbox-Auswahl
- [ ] Listing-Detailseite mit vollständigen Daten
- [ ] Export von Listings (CSV/JSON)

## Nutzung

### 1. Deal Inbox öffnen
Klick auf "Deal Inbox" im Menü oder gehe zu `/deal-inbox`

### 2. Listings filtern
- Status: Neue, Interessante, Beobachtete, etc.
- Presets: "Heute neu", "Nur Deutschland", "Auktionen", etc.
- Freie Filter: Titel, Land, Preis, Zeitraum

### 3. Status ändern
POST an `/listings/{id}/status` mit Formular (wird durch UI gesendet)

### 4. Notiz hinzufügen
POST an `/listings/{id}/note` mit Notiz-Text

### 5. Gekaufte Listing verwalten
Status auf "purchased" setzen → purchased_at wird automatisch gesetzt

## Limitations (MVP)

- Schnellaktions-Buttons sind noch nicht in allen Tabellen integriert (kommt in Phase 3)
- Notiz-Feld ist minimal (kommt in Phase 4)
- Analytics sind nicht implementiert (kommt in Phase 4)
- Smart-Suggestions nicht implementiert (kommt in Phase 5)

## Sicherheit & Validierung

- Nur eigene Listings ändern (user_id noch nicht implementiert – alle Listings sind öffentlich editierbar in MVP)
- Ungültige Status werden abgelehnt
- Soft-deleted Listings können nicht geändert werden (außer restore)
- Timestamps werden serverseitig gesetzt

## Testing

```bash
# Status setzen
curl -X POST http://localhost:8000/listings/1/status \
  -d "status=interesting&return_url=/listings"

# Notiz speichern
curl -X POST http://localhost:8000/listings/1/note \
  -d "note=Interessant,%20aber%20zu%20teuer"

# Deal Inbox filtern
curl http://localhost:8000/deal-inbox?status=interesting&country=DE
```
