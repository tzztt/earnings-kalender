"""Datenquellen: Yahoo Finance (kostenlos, kein Key), optional Finnhub (kostenloser Key), Demo.

Einheitliches Earnings-Format (dict):
  date      'YYYY-MM-DD' (Datum in Börsen-Zeitzone)
  time      'bmo' (vor Börsenstart) | 'amc' (nach Börsenschluss) | 'dmh' (während Handel) | None
  ts        ISO-Zeitstempel UTC oder None
  confirmed True/False/None
  eps_est, eps_act, rev_est, rev_act   Zahlen oder None
  source    'yahoo' | 'finnhub' | 'demo'
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

log = logging.getLogger("ecal")


def classify_time(ts_utc: datetime | None, tz: str) -> tuple[str | None, str]:
    """Liefert (Kategorie, Datum in Börsenzeit)."""
    if ts_utc is None:
        return None, ""
    local = ts_utc.astimezone(ZoneInfo(tz))
    if local.hour == 0 and local.minute == 0:
        return None, local.date().isoformat()
    minutes = local.hour * 60 + local.minute
    open_m, close_m = (9 * 60 + 30, 16 * 60) if tz.startswith("America/") else (9 * 60, 17 * 60 + 30)
    if minutes < open_m:
        cat = "bmo"
    elif minutes >= close_m:
        cat = "amc"
    else:
        cat = "dmh"
    return cat, local.date().isoformat()


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


# ====================================================================== Yahoo

class YahooProvider:
    name = "yahoo"

    def __init__(self, pause: float = 0.4, period: str = "2y"):
        import yfinance as yf  # erst hier importieren, damit der Demo-Modus ohne yfinance läuft
        self.yf = yf
        self.pause = pause
        self.period = period

    def histories(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), 40):
            chunk = symbols[i:i + 40]
            df = self._download(chunk)
            for s in chunk:
                sub = None
                if df is not None and not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        if s in df.columns.get_level_values(0):
                            sub = df[s]
                    else:
                        sub = df
                if sub is None or sub.dropna(subset=["Close"]).empty:
                    sub = self._single_history(s)
                if sub is not None and not sub.dropna(subset=["Close"]).empty:
                    sub = sub.dropna(subset=["Close"]).copy()
                    sub.index = pd.to_datetime(sub.index).tz_localize(None) if getattr(sub.index, "tz", None) else pd.to_datetime(sub.index)
                    out[s] = sub[["Open", "High", "Low", "Close", "Volume"]]
                else:
                    log.warning("Keine Kursdaten für %s", s)
            time.sleep(self.pause * 3)
        return out

    def _download(self, chunk):
        for attempt in range(3):
            try:
                return self.yf.download(chunk, period=self.period, interval="1d", group_by="ticker",
                                        auto_adjust=False, threads=True, progress=False)
            except Exception as e:  # noqa: BLE001
                log.warning("Download-Fehler (%s), neuer Versuch …", e)
                time.sleep(20 * (attempt + 1))
        return None

    def _single_history(self, s):
        try:
            time.sleep(self.pause)
            return self.yf.Ticker(s).history(period=self.period, interval="1d", auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.warning("Historie %s: %s", s, e)
            return None

    def meta_and_earnings(self, symbol: str, tz: str) -> tuple[dict, list[dict]]:
        """Name/Währung + Earnings-Termine (Zukunft und Vergangenheit)."""
        t = self.yf.Ticker(symbol)
        info = self._retry(lambda: t.info) or {}
        meta = {"name": info.get("shortName") or info.get("longName"), "currency": info.get("currency")}
        recs: dict[str, dict] = {}

        # 1) Historie + nächste Termine mit EPS-Schätzung/-Ist
        ed = self._retry(lambda: t.get_earnings_dates(limit=12))
        if ed is not None and not ed.empty:
            for ts, row in ed.iterrows():
                ts_utc = pd.Timestamp(ts).tz_convert("UTC").to_pydatetime() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).tz_localize(tz).tz_convert("UTC").to_pydatetime()
                cat, d = classify_time(ts_utc, tz)
                recs[d] = {
                    "date": d, "time": cat, "ts": ts_utc.isoformat() if cat else None, "confirmed": None,
                    "eps_est": _num(row.get("EPS Estimate")), "eps_act": _num(row.get("Reported EPS")),
                    "rev_est": None, "rev_act": None, "source": "yahoo",
                }

        # 2) Nächster Termin aus info (inkl. "geschätzt"-Flag) und calendar (Umsatz-Schätzung)
        ts = info.get("earningsTimestampStart") or info.get("earningsTimestamp")
        if ts:
            ts_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            cat, d = classify_time(ts_utc, tz)
            if ts_utc > datetime.now(timezone.utc) - timedelta(days=1):
                r = _closest(recs, d) or recs.setdefault(d, {"date": d, "time": cat, "ts": None, "confirmed": None,
                                                             "eps_est": None, "eps_act": None, "rev_est": None,
                                                             "rev_act": None, "source": "yahoo"})
                if r["date"] != d:  # Termin wurde verschoben -> info hat Vorrang
                    recs.pop(r["date"], None)
                    r["date"] = d
                    recs[d] = r
                r["time"] = cat or r["time"]
                r["ts"] = ts_utc.isoformat() if cat else r["ts"]
                est = info.get("isEarningsDateEstimate")
                r["confirmed"] = (not est) if est is not None else None
        cal = self._retry(lambda: t.calendar) or {}
        if isinstance(cal, dict):
            nxt = min((r for r in recs.values() if r["date"] >= date.today().isoformat()),
                      key=lambda r: r["date"], default=None)
            if nxt:
                nxt["rev_est"] = _num(cal.get("Revenue Average"))
                nxt["eps_est"] = nxt["eps_est"] or _num(cal.get("Earnings Average"))
                if nxt["confirmed"] is None and isinstance(cal.get("Earnings Date"), list):
                    nxt["confirmed"] = len(cal["Earnings Date"]) == 1

        # 3) Umsatz-Ist aus Quartals-GuV (Quartalsende 0–110 Tage vor dem Termin)
        q = self._retry(lambda: t.quarterly_income_stmt)
        if q is not None and not getattr(q, "empty", True) and "Total Revenue" in q.index:
            rev = q.loc["Total Revenue"].dropna()
            for r in recs.values():
                if r["eps_act"] is None:
                    continue
                d = date.fromisoformat(r["date"])
                for end, val in rev.items():
                    if 0 <= (d - pd.Timestamp(end).date()).days <= 110:
                        r["rev_act"] = float(val)
                        break
        time.sleep(self.pause)
        return meta, sorted(recs.values(), key=lambda r: r["date"])

    def _retry(self, fn):
        for attempt in range(3):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "Rate" in msg or "Too Many" in msg or "429" in msg:
                    time.sleep(30 * (attempt + 1))
                    continue
                return None
        return None


def _closest(recs: dict, d: str, days: int = 10):
    target = date.fromisoformat(d)
    best = None
    for r in recs.values():
        diff = abs((date.fromisoformat(r["date"]) - target).days)
        if diff <= days and r["eps_act"] is None and (best is None or diff < best[0]):
            best = (diff, r)
    return best[1] if best else None


# ====================================================================== Finnhub (optional)

class FinnhubCalendar:
    """US-Earnings-Kalender von Finnhub (kostenloser Key, 60 Abfragen/Minute).

    Liefert Termin, Zeitpunkt (bmo/amc), EPS und Umsatz (Schätzung + Ist) für alle
    US-Werte mit wenigen Abfragen. Wird nur genutzt, wenn FINNHUB_API_KEY gesetzt ist.
    """

    def __init__(self, key: str):
        import requests
        self.s = requests.Session()
        self.key = key

    def fetch(self, start: date, end: date) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        cur = start
        while cur <= end:
            stop = min(cur + timedelta(days=30), end)
            try:
                r = self.s.get("https://finnhub.io/api/v1/calendar/earnings",
                               params={"from": cur.isoformat(), "to": stop.isoformat(), "token": self.key}, timeout=30)
                r.raise_for_status()
                for e in r.json().get("earningsCalendar", []):
                    out.setdefault(e["symbol"].replace(".", "-"), []).append({
                        "date": e["date"], "time": e.get("hour") or None, "ts": None, "confirmed": None,
                        "eps_est": _num(e.get("epsEstimate")), "eps_act": _num(e.get("epsActual")),
                        "rev_est": _num(e.get("revenueEstimate")), "rev_act": _num(e.get("revenueActual")),
                        "source": "finnhub",
                    })
            except Exception as ex:  # noqa: BLE001
                log.warning("Finnhub %s–%s: %s", cur, stop, ex)
            cur = stop + timedelta(days=1)
            time.sleep(1.1)
        return out


def merge_earnings(base: list[dict], extra: list[dict]) -> list[dict]:
    """Finnhub-Daten über Yahoo legen (Treffer ±4 Tage), fehlende Termine ergänzen."""
    out = [dict(r) for r in base]
    for e in extra:
        ed = date.fromisoformat(e["date"])
        match = None
        for r in out:
            if abs((date.fromisoformat(r["date"]) - ed).days) <= 4:
                match = r
                break
        if match is None:
            out.append(dict(e))
            continue
        if ed >= date.today() or match["eps_act"] is None:
            match["date"] = e["date"]
        for k in ("time", "eps_est", "eps_act", "rev_est", "rev_act"):
            if e.get(k) is not None:
                match[k] = e[k]
        match["source"] = "yahoo+finnhub"
    dedup = {r["date"]: r for r in sorted(out, key=lambda r: r["date"])}
    return list(dedup.values())


# ====================================================================== Demo (offline)

class DemoProvider:
    """Erzeugt plausible Zufallsdaten – zum Testen ohne Internet."""
    name = "demo"

    def __init__(self, seed: int = 7):
        self.seed = seed

    def _rng(self, symbol):
        return random.Random(f"{self.seed}-{symbol}")

    def histories(self, symbols):
        out = {}
        end = pd.Timestamp(date.today()) - pd.Timedelta(days=1)
        idx = pd.bdate_range(end=end, periods=500)
        for s in symbols:
            rng = self._rng(s)
            p = rng.uniform(5, 400)
            vol = rng.uniform(0.018, 0.05)
            drift = rng.uniform(-0.0005, 0.0015)
            closes, opens, highs, lows, vols = [], [], [], [], []
            base_vol = rng.uniform(2e5, 4e7)
            for _ in idx:
                o = p * (1 + rng.gauss(0, vol / 3))
                p = max(0.5, p * (1 + drift + rng.gauss(0, vol)))
                hi = max(o, p) * (1 + abs(rng.gauss(0, vol / 2)))
                lo = min(o, p) * (1 - abs(rng.gauss(0, vol / 2)))
                opens.append(o); closes.append(p); highs.append(hi); lows.append(lo)
                vols.append(base_vol * rng.uniform(0.5, 1.8))
            out[s] = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=idx)
        return out

    def meta_and_earnings(self, symbol, tz):
        rng = self._rng(symbol + "e")
        today = date.today()
        nxt = today + timedelta(days=rng.randint(-3, 85))
        recs = []
        eps = rng.uniform(-0.5, 3)
        rev = rng.uniform(5e7, 4e10)
        for q in range(5, -1, -1):
            d = nxt - timedelta(days=91 * q + rng.randint(-3, 3) * (q > 0))
            while d.weekday() >= 5:
                d += timedelta(days=1)
            past = d < today
            est = eps * rng.uniform(0.9, 1.1)
            recs.append({
                "date": d.isoformat(), "time": rng.choice(["bmo", "amc", "amc", None]), "ts": None,
                "confirmed": True if past else rng.random() > 0.4,
                "eps_est": round(est, 2), "eps_act": round(est * rng.uniform(0.85, 1.2), 2) if past else None,
                "rev_est": rev, "rev_act": rev * rng.uniform(0.95, 1.06) if past else None, "source": "demo",
            })
            eps *= rng.uniform(0.95, 1.12)
            rev *= rng.uniform(0.97, 1.1)
        return {"name": f"{symbol} Demo AG", "currency": "EUR" if "." in symbol else "USD"}, recs


def finnhub_from_env():
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    return FinnhubCalendar(key) if key else None
