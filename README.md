# Earnings-Kalender für die TradingView-Watchlist

Erzeugt automatisch einen abonnierbaren Kalender (`earnings.ics`) für Apple Kalender:

- **Kommende Earnings** jeder Aktie der Watchlist mit Zeitpunkt (vor-/nachbörslich, Uhrzeit DE) und Status (bestätigt/vorläufig)
- **Kurs & Technik:** Kurs, RSI(14), Volumen vs. Ø 20 Tage, Handelswert, ATR, SMA 20/50/200, 52-Wochen-Spanne
- **Kursmarken:** die nächsten Widerstände und Unterstützungen (Swing-Hochs/-Tiefs)
- **CRV in drei Varianten** (siehe unten)
- **Nach den Earnings wird nachgetragen:** EPS und Umsatz vs. Erwartung, Beat/Miss, Kursreaktion, Volumen am Reaktionstag und der Stand vor den Zahlen
- **Hyperscaler/Kunden:** Beim Zulieferer stehen die Earnings-Termine seiner Kunden (z. B. bei VRT: MSFT, AMZN, GOOGL …), beim Kunden die betroffenen Zulieferer
- 💼 markiert Werte aus dem Abschnitt PORTFOLIO

Kosten: **0 €** (GitHub-Konto, Yahoo-Daten; optional ein kostenloser Finnhub-Key).

## Titel im Kalender

| Symbol | Bedeutung |
| --- | --- |
| 📅 | kommende Earnings |
| 🏢 | kommende Earnings eines Hyperscalers/Großkunden |
| ✅ / ❌ / ➖ | gemeldet: Beat / Miss / im Rahmen (±2 % zur EPS-Erwartung) |
| ⏳ | Termin vorbei, Ist-Zahlen noch nicht in den Daten |
| 💼 | im Portfolio |
| (vorl.) | Termin noch nicht vom Unternehmen bestätigt |

## Die drei CRV-Varianten (Long)

Alle drei nutzen dasselbe **Ziel**: den nächsten Widerstand, der mindestens 1 ATR über dem Einstieg liegt
(sonst 52W-Hoch, am Allzeithoch Kurs + 3 ATR).

| Variante | Einstieg | Stop | Wofür |
| --- | --- | --- | --- |
| **A Struktur** | aktueller Kurs | 0,5 % unter dem nächsten Swing-Tief | klassisches Chartbild: Stop dort, wo die Struktur bricht |
| **B ATR** | aktueller Kurs | Kurs − 1,5 × ATR(14) | volatilitätsbasiert; gut bei Werten ohne klare Tiefs |
| **C Pullback** | höchste Unterstützung unter dem Kurs (SMA20, SMA50 oder Swing-Tief, max. 15 % entfernt) | Einstieg − 1 × ATR | geplanter Einstieg im Rücksetzer statt hinterherzulaufen |

Alle Faktoren lassen sich in `config.yaml` ändern. Welche Variante im Titel steht, legt `crv_im_titel` fest.

> Hinweis: Das CRV ist eine regelbasierte Rechenhilfe, keine Anlageberatung. Vor Earnings sind Gaps
> über den Stop hinaus häufig. Die Zeile „Ø Kursreaktion letzte Earnings“ zeigt, wie groß diese Sprünge zuletzt waren.

## Einrichtung (einmalig, ca. 15 Minuten)

### 1. GitHub-Repository anlegen
1. Kostenloses Konto auf [github.com](https://github.com) anlegen.
2. **New repository** → Name z. B. `earnings-kalender` → **Private** → Create.
3. **Add file → Upload files** → den gesamten Inhalt dieses Ordners hochladen (auch den Ordner `.github`).
   Tipp: Auf dem Mac versteckte Ordner im Finder mit `Cmd + Shift + .` einblenden.

### 2. Geheimes Gist anlegen (hier liegt später der Kalender)
1. [gist.github.com](https://gist.github.com) öffnen.
2. Dateiname `earnings.ics`, Inhalt z. B. `leer`, dann **Create secret gist**.
3. Die ID aus der Adresse kopieren: `https://gist.github.com/<name>/`**`a1b2c3...`**

> Ein „secret“ Gist ist nicht öffentlich gelistet. Wer die Adresse kennt, kann es aber lesen.
> Also die Abo-URL nicht weitergeben.

### 3. Token erstellen
1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token.
2. Nur das Häkchen **gist** setzen, Ablauf z. B. 1 Jahr → Token kopieren.

### 4. Secrets im Repository hinterlegen
Repository → Settings → Secrets and variables → Actions → **New repository secret**:

| Name | Wert |
| --- | --- |
| `GIST_TOKEN` | der Token aus Schritt 3 |
| `GIST_ID` | die Gist-ID aus Schritt 2 |
| `FINNHUB_API_KEY` | *optional*: kostenloser Key von [finnhub.io](https://finnhub.io). Verbessert US-Termine, Uhrzeiten und Umsatzzahlen. |

### 5. Ersten Lauf starten
Repository → **Actions** → „Earnings-Kalender aktualisieren“ → **Run workflow**.
Der Lauf dauert 5–15 Minuten. Am Ende steht im Log die Abo-URL, z. B.:
`webcal://gist.githubusercontent.com/<name>/<gist-id>/raw/earnings.ics`

Danach läuft alles automatisch Mo–Fr zweimal täglich.

### 6. In Apple Kalender abonnieren
**Mac (empfohlen, damit es per iCloud auf alle Geräte kommt):**
Kalender → Ablage → **Neues Kalenderabonnement** → URL einfügen → Ort: **iCloud** →
Automatisch aktualisieren: **Jede Stunde**. „Hinweise entfernen“ abwählen, wenn du die Erinnerungen willst.

**Nur iPhone:** Einstellungen → Apps → Kalender → Kalender-Accounts → Account hinzufügen →
Andere → **Kalenderabo hinzufügen** → URL einfügen.

## Watchlist aktualisieren
Neuen Export aus TradingView als `watchlist.txt` ins Repository hochladen (Add file → Upload files,
gleicher Name überschreibt). Beim nächsten Lauf ist der Kalender angepasst.

## Prüfen & anpassen
- **`report.md`** (liegt im Gist und im Repository unter `output/`): listet Werte ohne Kursdaten oder
  ohne Earnings-Termin.
- **`symbols_override.yaml`:** Deutsche Zweitlistings (Tradegate/Gettex/L&S) auf die Heimatbörse
  umstellen, z. B. Nokia → `NOKIA.HE`. Einige Einträge sind vorbereitet; die mit „prüfen“ bitte kontrollieren.
  Für einige Kürzel konnte ich das Unternehmen nicht sicher zuordnen. Sie sind als `???` markiert.
- **`customers.yaml`:** Zuordnung Hyperscaler/Kunde → Zulieferer. Ist ein **Entwurf** und sollte geprüft werden.
- **`config.yaml`:** CRV-Faktoren, Zeitraum, Erinnerung, Kalendername.

## Grenzen
- **Datenquelle Yahoo** ist kostenlos, aber inoffiziell. Bei Sperren wird automatisch wiederholt.
  Fällt ein Lauf aus, bleibt der letzte Kalender bestehen.
- **Werte sind Momentaufnahmen** vom letzten Lauf, nicht live.
- **Apple lädt Abos** je nach Einstellung neu (z. B. stündlich). Das Gist selbst wird 2× täglich aktualisiert.
- **Termine kleiner/ausländischer Werte** sind teils nur geschätzt oder fehlen.

## Lokal testen (optional)
```bash
pip install -r requirements.txt
python -m ecal.build --demo    # Zufallsdaten, ohne Internet
python -m ecal.build           # echte Daten
```
Ergebnis: `output/earnings.ics`, `output/dashboard.json` (Daten für die spätere App), `output/report.md`.

## Projektaufbau
```
watchlist.txt            TradingView-Export
config.yaml              Einstellungen
symbols_override.yaml    TradingView -> Yahoo-Symbole
customers.yaml           Hyperscaler/Kunden -> Zulieferer
ecal/watchlist.py        Watchlist einlesen, Symbole auflösen
ecal/providers.py        Yahoo, Finnhub (optional), Demo-Daten
ecal/analysis.py         RSI, ATR, SMA, Kursmarken, CRV, Earnings-Reaktion
ecal/ics.py              Kalenderdatei schreiben
ecal/build.py            Ablauf + Texte im Kalender
ecal/publish.py          Upload ins Gist
.github/workflows/update.yml   automatischer Lauf
data/state.json          Stand vor den Earnings (wird automatisch gepflegt)
```
