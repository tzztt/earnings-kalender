"""Lädt earnings.ics, dashboard.json und report.md in ein (geheimes) GitHub-Gist.

Benötigt die Umgebungsvariablen GIST_TOKEN (Token mit Recht "gist") und GIST_ID.
Die Abo-URL für den Apple-Kalender lautet danach:
  webcal://gist.githubusercontent.com/<DEIN-NAME>/<GIST_ID>/raw/earnings.ics
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FILES = ["earnings.ics", "dashboard.json", "report.md"]


def main():
    token = os.environ.get("GIST_TOKEN", "").strip()
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not token or not gist_id:
        print("GIST_TOKEN oder GIST_ID fehlt – Veröffentlichung übersprungen.")
        return 0
    out = ROOT / "output"
    files = {n: {"content": (out / n).read_text(encoding="utf-8")} for n in FILES if (out / n).exists()}
    if "earnings.ics" not in files:
        print("output/earnings.ics fehlt – nichts veröffentlicht.")
        return 1
    r = requests.patch(f"https://api.github.com/gists/{gist_id}",
                       headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                       json={"files": files}, timeout=60)
    if r.status_code >= 300:
        print(f"Fehler beim Gist-Upload: {r.status_code} {r.text[:300]}")
        return 1
    owner = r.json().get("owner", {}).get("login", "<DEIN-NAME>")
    print("Veröffentlicht. Abo-URL für Apple Kalender:")
    print(f"  webcal://gist.githubusercontent.com/{owner}/{gist_id}/raw/earnings.ics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
