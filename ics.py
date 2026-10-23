"""Minimaler, standardkonformer iCalendar-Writer (RFC 5545) – ohne Zusatzbibliothek."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Zeilen auf max. 75 Byte umbrechen, ohne UTF-8-Zeichen zu zerschneiden."""
    out, cur, size = [], "", 0
    limit = 75
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > limit:
            out.append(cur)
            cur, size, limit = " " + ch, 1 + b, 75
        else:
            cur += ch
            size += b
    out.append(cur)
    return "\r\n".join(out)


class Calendar:
    def __init__(self, name: str, tz: str = "Europe/Berlin", ttl_hours: int = 4):
        self.name = name
        self.tz = tz
        self.ttl = ttl_hours
        self.events: list[list[str]] = []

    def add_allday(self, uid: str, day: date, summary: str, description: str,
                   url: str | None = None, categories: list[str] | None = None,
                   alarm_minutes: int | None = None):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ev = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"LAST-MODIFIED:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(description)}",
            "TRANSP:TRANSPARENT",
        ]
        if url:
            ev.append(f"URL:{url}")
        if categories:
            ev.append("CATEGORIES:" + ",".join(_escape(c) for c in categories))
        if alarm_minutes:
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(summary)}",
                   f"TRIGGER:-PT{int(alarm_minutes)}M", "END:VALARM"]
        ev.append("END:VEVENT")
        self.events.append(ev)

    def to_ics(self) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//earnings-kalender//DE",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:{_escape(self.name)}",
            f"X-WR-TIMEZONE:{self.tz}",
            "X-WR-CALDESC:Earnings-Termine der Watchlist mit Technik und CRV",
            f"REFRESH-INTERVAL;VALUE=DURATION:PT{self.ttl}H",
            f"X-PUBLISHED-TTL:PT{self.ttl}H",
        ]
        for ev in self.events:
            lines += ev
        lines.append("END:VCALENDAR")
        return "\r\n".join(_fold(l) for l in lines) + "\r\n"
