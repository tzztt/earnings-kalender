"""Earnings-Wächter: prüft rund um die Meldezeit alle 5 Minuten, ob Ist-Zahlen da sind.

Ablauf (stündlich per GitHub Actions gestartet, siehe .github/workflows/watch.yml):
  1. --check          Gibt es in der nächsten Stunde etwas zu beobachten? (nur Standardbibliothek, ~1 Sekunde)
  2. (ohne Option)    Bis zu 55 Minuten lang alle 5 Minuten abfragen. Sobald neue Zahlen da sind:
                      Push-Nachricht über ntfy, dann Kalender + Dashboard neu bauen und veröffentlichen.

Die Terminliste (data/watch.json) schreibt der normale Lauf (ecal.build).
Bereits gemeldete Werte stehen in data/watch_state.json, damit nichts doppelt gemeldet wird.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WATCH = ROOT / "data" / "watch.json"
STATE = ROOT / "data" / "watch_state.json"
log = logging.getLogger("ecal.watch")

# Fenster, wenn die genaue Uhrzeit nicht bekannt ist (Ortszeit der Börse)
LOCAL_WINDOW = {"bmo": ((6, 0), (11, 0)), "amc": ((16, 0), (22, 30)), None: ((7, 0), (22, 30))}
BEFORE = timedelta(minutes=5)
AFTER = timedelta(minutes=120)


def window(item: dict) -> tuple[datetime, datetime]:
    """Beobachtungsfenster in UTC."""
    if item.get("ts"):
        ts = datetime.fromisoformat(item["ts"]).astimezone(timezone.utc)
        return ts - BEFORE, ts + AFTER
    tz = ZoneInfo(item.get("tz") or "America/New_York")
    (h0, m0), (h1, m1) = LOCAL_WINDOW.get(item.get("time"), LOCAL_WINDOW[None])
    d = date.fromisoformat(item["date"])
    start = datetime(d.year, d.month, d.day, h0, m0, tzinfo=tz)
    end = datetime(d.year, d.month, d.day, h1, m1, tzinfo=tz)
    if item.get("time") == "amc":                      # Nachzügler bis kurz nach Mitternacht
        end += timedelta(hours=2)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def key(item: dict) -> str:
    return f"{item['yahoo']}|{item['date']}"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pending(now: datetime, horizon: timedelta = timedelta(0)):
    """Einträge, deren Fenster jetzt (bzw. bis now+horizon) offen ist und die noch nicht gemeldet wurden."""
    done = load_json(STATE, {}).get("notified", {})
    out = []
    for it in load_json(WATCH, {}).get("items", []):
        if key(it) in done:
            continue
        a, b = window(it)
        if a <= now + horizon and now <= b:
            out.append((a, b, it))
    return sorted(out, key=lambda x: x[0])


# ------------------------------------------------------------------ Benachrichtigung

def notify(title: str, message: str, tags: list[str], priority: int, dry: bool):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if dry or not topic:
        print(f"[ntfy{' (Trockenlauf)' if dry else ' – NTFY_TOPIC fehlt'}] {title}\n{message}\n")
        return
    import requests
    body = {"topic": topic, "title": title, "message": message, "tags": tags, "priority": priority}
    if os.environ.get("APP_URL"):
        body["click"] = os.environ["APP_URL"]
    try:
        r = requests.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), json=body, timeout=20)
        if r.status_code >= 300:
            log.warning("ntfy: %s %s", r.status_code, r.text[:200])
    except Exception as ex:                                  # Benachrichtigung darf den Lauf nie stoppen
        log.warning("ntfy fehlgeschlagen: %s", ex)


def describe(it: dict, rec: dict) -> tuple[str, str, list[str]]:
    from .build import result_lines, TIME_LABEL
    lines, label, icon = result_lines(rec, "")
    tag = {"✅": "white_check_mark", "❌": "x", "➖": "heavy_minus_sign"}.get(icon, "bar_chart")
    when = TIME_LABEL.get(it.get("time") or "", "")
    title = f"{icon} {it['ticker']}: {label}"
    msg = [it["name"] + (f" · gemeldet {when}" if when else "")] + lines
    return title, "\n".join(msg), [tag]


# ------------------------------------------------------------------ Abfrage

def fetch_new(items: list[dict], prov, fh_data: dict) -> list[tuple[dict, dict]]:
    from .providers import merge_earnings
    hits = []
    for it in items:
        try:
            _, earn = prov.meta_and_earnings(it["yahoo"], it["tz"])
            if it["yahoo"] in fh_data:
                earn = merge_earnings(earn, fh_data[it["yahoo"]])
        except Exception as ex:
            log.warning("%s: Abruf fehlgeschlagen (%s)", it["ticker"], ex)
            continue
        d = date.fromisoformat(it["date"])
        rec = min((r for r in earn if abs((date.fromisoformat(r["date"]) - d).days) <= 1 and r.get("eps_act") is not None),
                  key=lambda r: abs((date.fromisoformat(r["date"]) - d).days), default=None)
        if rec:
            hits.append((it, rec))
    return hits


def rebuild(demo: bool) -> None:
    """Kalender + Dashboard neu erzeugen und im Gist veröffentlichen."""
    args = ["--demo"] if demo else []
    for mod, extra in (("ecal.build", args), ("ecal.publish", [])):
        r = subprocess.run([sys.executable, "-m", mod, *extra], cwd=ROOT)
        if r.returncode != 0:
            log.warning("%s endete mit Code %s", mod, r.returncode)


def run(a) -> int:
    from .providers import DemoProvider, YahooProvider, finnhub_from_env
    prov = DemoProvider() if a.demo else YahooProvider(pause=0.2, period="1mo")
    fh = None if a.demo else finnhub_from_env()
    started = datetime.now(timezone.utc)
    stop = started + timedelta(minutes=a.slot_minutes)
    while True:
        now = datetime.now(timezone.utc)
        due = [it for a_, b_, it in pending(now)]
        if due:
            fh_data = fh.fetch(date.today() - timedelta(days=1), date.today() + timedelta(days=1)) if fh else {}
            log.info("Prüfe %d Wert(e): %s", len(due), ", ".join(i["ticker"] for i in due))
            hits = fetch_new(due, prov, fh_data)
            if hits:
                st = load_json(STATE, {"notified": {}})
                for it, rec in hits:
                    title, msg, tags = describe(it, rec)
                    notify(title, msg, tags, 4 if it.get("portfolio") else 3, a.dry)
                    st.setdefault("notified", {})[key(it)] = now.isoformat()
                cutoff = (now - timedelta(days=30)).isoformat()
                st["notified"] = {k: v for k, v in st["notified"].items() if v >= cutoff}
                STATE.parent.mkdir(parents=True, exist_ok=True)
                STATE.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf-8")
                if not a.no_rebuild:
                    rebuild(a.demo)
        else:
            nxt = pending(now, timedelta(minutes=a.slot_minutes))
            if not nxt or a.once:
                log.info("Nichts mehr zu beobachten.")
                return 0
            wait = max(0, (nxt[0][0] - now).total_seconds())
            log.info("Nächstes Fenster in %.0f Minuten (%s).", wait / 60, nxt[0][2]["ticker"])
            if now + timedelta(seconds=wait) >= stop:
                return 0
            time.sleep(wait)
            continue
        if a.once or datetime.now(timezone.utc) + timedelta(seconds=a.interval) >= stop:
            return 0
        time.sleep(a.interval)


def check(a) -> int:
    """Gate-Schritt: schreibt due=true/false für GitHub Actions."""
    now = datetime.now(timezone.utc)
    due = bool(pending(now, timedelta(minutes=a.slot_minutes)))
    print(f"due={'true' if due else 'false'}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"due={'true' if due else 'false'}\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Earnings-Wächter")
    p.add_argument("--check", action="store_true", help="nur prüfen, ob in der nächsten Stunde etwas ansteht")
    p.add_argument("--slot-minutes", type=int, default=55)
    p.add_argument("--interval", type=int, default=300, help="Sekunden zwischen den Abfragen")
    p.add_argument("--once", action="store_true", help="nur eine Runde (Test)")
    p.add_argument("--dry", action="store_true", help="keine Nachricht senden, nur ausgeben")
    p.add_argument("--no-rebuild", action="store_true")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return check(a) if a.check else run(a)


if __name__ == "__main__":
    sys.exit(main())
