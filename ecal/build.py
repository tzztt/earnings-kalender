"""Hauptprogramm: Watchlist -> Daten -> Kennzahlen -> earnings.ics + dashboard.json.

Aufruf:
  python -m ecal.build            # echte Daten (Yahoo, optional Finnhub)
  python -m ecal.build --demo     # Zufallsdaten zum Testen ohne Internet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .analysis import earnings_reaction, technical_snapshot
from .ics import Calendar
from .providers import DemoProvider, finnhub_from_env, merge_earnings
from .watchlist import Entry, SUFFIX_TZ, parse_watchlist, resolve_symbols

log = logging.getLogger("ecal")
ROOT = Path(__file__).resolve().parent.parent
BERLIN = ZoneInfo("Europe/Berlin")

TIME_LABEL = {"bmo": "vorbörslich", "amc": "nachbörslich", "dmh": "während des Handels"}
CUR = {"USD": "$", "EUR": "€", "GBP": "£", "GBp": "GBp", "HKD": "HK$", "CNY": "¥", "KRW": "₩", "AUD": "A$", "JPY": "¥"}


# ====================================================================== Formatierung

def num(x, d=2, sign=False):
    if x is None:
        return "–"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".").replace("-", "−")


def pct(x, d=1):
    return "–" if x is None else num(x, d, sign=True) + " %"


def big(x, cur=""):
    if x is None:
        return "–"
    a = abs(x)
    for div, unit in ((1e12, "Bio"), (1e9, "Mrd"), (1e6, "Mio"), (1e3, "Tsd")):
        if a >= div:
            return f"{num(x / div, 1)} {unit}{(' ' + cur) if cur else ''}"
    return num(x, 0) + ((" " + cur) if cur else "")


def price(x, cur):
    if x is None:
        return "–"
    d = 2 if x >= 1 else 4
    return f"{num(x, d)} {CUR.get(cur or '', cur or '')}".strip()


def dshort(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%d.%m.}"


def dlong(iso: str) -> str:
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    d = date.fromisoformat(iso)
    return f"{wd[d.weekday()]}, {d:%d.%m.%Y}"


def berlin_time(ts: str | None) -> str:
    if not ts:
        return ""
    t = datetime.fromisoformat(ts).astimezone(BERLIN)
    return f"{t:%H:%M} Uhr (DE)"


# ====================================================================== Kunden

def load_customers(path: Path, entries: list[Entry]):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    by_ticker = {e.ticker: e for e in entries}
    by_tv = {e.tv: e for e in entries}
    customers: dict[str, dict] = {}
    supplier_of: dict[str, list[str]] = {}
    extra: list[Entry] = []
    for cust, spec in data.items():
        cust = str(cust).upper()
        spec = spec or {}
        e = by_ticker.get(cust)
        if e is None:
            sym = str(spec.get("yahoo", cust)).upper()
            e = Entry(tv=spec.get("tradingview", f"NASDAQ:{cust}"), exchange="", ticker=cust,
                      section="Kunde (nicht in Watchlist)", yahoo=sym, extra=True,
                      tz=SUFFIX_TZ.get(sym[sym.rfind("."):] if "." in sym else "", "America/New_York"))
            extra.append(e)
            by_ticker[cust] = e
        sups = []
        for s in spec.get("zulieferer", []) or []:
            s = str(s).upper()
            se = by_tv.get(s) or by_ticker.get(s.split(":")[-1])
            if se is None:
                log.info("Kunde %s: Zulieferer %s nicht in der Watchlist", cust, s)
                continue
            sups.append(se.ticker)
            supplier_of.setdefault(se.ticker, []).append(cust)
        customers[cust] = {"name": spec.get("name", cust), "typ": spec.get("typ", "Kunde"), "suppliers": sups}
    return customers, supplier_of, extra


# ====================================================================== Texte

def tech_block(s: dict, cur: str) -> list[str]:
    if not s:
        return ["Keine Kursdaten verfügbar."]
    L = [f"KURS & TECHNIK (Stand {dshort(s['asof'])})",
         f"Kurs: {price(s['close'], cur)} ({pct(s['change_pct'])}) · RSI(14): {num(s['rsi'], 1)}{_rsi_tag(s['rsi'])}"]
    if s.get("volume"):
        L.append(f"Volumen: {big(s['volume'])} Stück ({num(s['rel_volume'], 1)}× Ø20T) · Ø20T: {big(s['volume_avg'])}")
        L.append(f"Handelswert: {big(s['volume'] * s['close'], CUR.get(cur or '', cur or ''))}")
    L.append(f"ATR(14): {price(s['atr'], cur)} ({num(s['atr_pct'], 1)} %)")
    smas = [(n, s[k]) for n, k in (("SMA20", "sma20"), ("SMA50", "sma50"), ("SMA200", "sma200")) if s.get(k)]
    if smas:
        L.append(" · ".join(f"{n} {num(v)}" for n, v in smas) + " → " + _trend(s))
    L.append(f"52W: {num(s['low52'])} – {num(s['high52'])} (Abstand Hoch {pct(s['dist_high52_pct'])})")
    L += ["", "KURSMARKEN",
          "Widerstände: " + (" · ".join(num(x) for x in s["resistances"]) or "keine (nahe Hoch)"),
          "Unterstützungen: " + (" · ".join(num(x) for x in s["supports"]) or "keine erkannt")]
    crv = s.get("crv") or {}
    if crv:
        L += ["", "CRV (Long)"]
        names = {"A": "A Struktur", "B": "B ATR", "C": "C Pullback"}
        for k in ("A", "B", "C"):
            v = crv.get(k)
            if not v:
                continue
            entry = f"Einstieg {num(v['entry'])} ({pct(v.get('entry_dist_pct'))}) · " if k == "C" else ""
            L.append(f"{names[k]}: {entry}Stop {num(v['stop'])} ({pct(v['stop_pct'])}) · "
                     f"Ziel {num(v['target'])} ({pct(v['target_pct'])}) → CRV {num(v['crv'], 1)}")
        tn = next((v["target_note"] for v in crv.values() if v), "")
        if tn:
            L.append(f"Ziel = {tn}")
    return L


def _rsi_tag(r):
    if r is None:
        return ""
    return " (überkauft)" if r >= 70 else " (überverkauft)" if r <= 30 else ""


def _trend(s):
    c = s["close"]
    above = [n for n, k in (("20", "sma20"), ("50", "sma50"), ("200", "sma200")) if s.get(k) and c > s[k]]
    total = sum(1 for k in ("sma20", "sma50", "sma200") if s.get(k))
    if len(above) == total:
        return "über allen"
    if not above:
        return "unter allen"
    return "über SMA" + "/".join(above)


def result_lines(r: dict, cur: str) -> tuple[list[str], str, str]:
    """Ergebniszeilen + Kurzlabel + Icon für vergangene Earnings."""
    L, label, icon = [], "Zahlen", "📊"
    if r.get("eps_act") is not None:
        surpr = None
        if r.get("eps_est") not in (None, 0):
            surpr = (r["eps_act"] - r["eps_est"]) / abs(r["eps_est"]) * 100
        L.append(f"EPS: {num(r['eps_act'])} vs. Erwartung {num(r.get('eps_est'))}"
                 + (f" ({pct(surpr)})" if surpr is not None else ""))
        if surpr is not None:
            if surpr > 2:
                label, icon = f"Beat EPS {pct(surpr, 0)}", "✅"
            elif surpr < -2:
                label, icon = f"Miss EPS {pct(surpr, 0)}", "❌"
            else:
                label, icon = "In-line", "➖"
    if r.get("rev_act") is not None:
        rs = ((r["rev_act"] / r["rev_est"] - 1) * 100) if r.get("rev_est") else None
        L.append(f"Umsatz: {big(r['rev_act'], CUR.get(cur or '', ''))} vs. Erwartung {big(r.get('rev_est'))}"
                 + (f" ({pct(rs)})" if rs is not None else ""))
    return L, label, icon


# ====================================================================== Hauptlogik

def run(args):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state.setdefault("earnings", {})

    entries = resolve_symbols(parse_watchlist(ROOT / args.watchlist), ROOT / "symbols_override.yaml")
    if args.limit:
        entries = entries[:args.limit]
    customers, supplier_of, extra = load_customers(ROOT / "customers.yaml", entries)
    all_entries = entries + extra
    active = [e for e in all_entries if e.yahoo]
    log.info("%d Werte aus der Watchlist, %d zusätzliche Kunden, %d abrufbar",
             len(entries), len(extra), len(active))

    if args.demo:
        prov = DemoProvider()
    else:
        from .providers import YahooProvider
        prov = YahooProvider(pause=cfg["daten"]["pause_sekunden"], period=cfg["daten"]["historie"])

    hist = prov.histories(sorted({e.yahoo for e in active}))

    today = date.today()
    fh = None if args.demo else finnhub_from_env()
    fh_data = fh.fetch(today - timedelta(days=cfg["kalender"]["vergangene_tage"]),
                       today + timedelta(days=cfg["kalender"]["zukunft_tage"])) if fh else {}

    rows: dict[str, dict] = {}
    for i, e in enumerate(active, 1):
        log.info("[%d/%d] %s (%s)", i, len(active), e.ticker, e.yahoo)
        meta, earn = prov.meta_and_earnings(e.yahoo, e.tz)
        if e.is_us and e.yahoo in fh_data:
            earn = merge_earnings(earn, fh_data[e.yahoo])
        df = hist.get(e.yahoo)
        snap = technical_snapshot(df, cfg) if df is not None else None
        # Earnings-Reaktionen der Vergangenheit
        reactions = []
        for r in earn:
            if r["date"] < today.isoformat() and df is not None:
                rx = earnings_reaction(df, date.fromisoformat(r["date"]), r["time"])
                if rx:
                    r["reaction"] = rx
                    reactions.append(abs(rx["move_pct"]))
        upcoming = [r for r in earn if r["date"] >= today.isoformat()]
        nxt = upcoming[0] if upcoming else None
        rows[e.ticker] = {
            "ticker": e.ticker, "tv": e.tv, "yahoo": e.yahoo, "section": e.section, "sections": e.sections,
            "portfolio": e.portfolio, "extra": e.extra, "note": e.note,
            "name": meta.get("name") or e.ticker, "currency": meta.get("currency") or ("USD" if e.is_us else ""),
            "tech": snap, "next": nxt, "history": [r for r in earn if r["date"] < today.isoformat()],
            "avg_move_pct": round(sum(reactions[-4:]) / len(reactions[-4:]), 2) if reactions else None,
            "customers": supplier_of.get(e.ticker, []),
            "suppliers": customers.get(e.ticker, {}).get("suppliers", []),
            "customer_type": customers.get(e.ticker, {}).get("typ"),
        }
        # Snapshot vor den Earnings sichern (letzter Lauf vor dem Termin gewinnt)
        if nxt and snap:
            asof = snap["asof"]
            if asof < nxt["date"] or (asof == nxt["date"] and nxt["time"] == "amc"):
                key = f"{e.yahoo}|{nxt['date']}"
                state["earnings"].setdefault(key, {})["pre"] = {
                    "asof": asof, "close": snap["close"], "rsi": snap["rsi"], "rel_volume": snap["rel_volume"],
                    "crv": {k: v["crv"] for k, v in (snap.get("crv") or {}).items()},
                }

    write_watch(rows, active, Path(args.watch_out), today)
    cal = build_calendar(rows, customers, state, cfg, today)
    (out_dir / "earnings.ics").write_text(cal.to_ics(), encoding="utf-8", newline="")
    write_dashboard(rows, customers, out_dir / "dashboard.json")
    write_report(all_entries, rows, out_dir / "report.md")
    _prune_state(state, today, cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    log.info("Fertig: %d Termine in %s", len(cal.events), out_dir / "earnings.ics")


def write_watch(rows, active, path: Path, today):
    """Terminliste für den Wächter (ecal.watch): alles, was in den nächsten Tagen gemeldet wird
    oder gestern gemeldet wurde und noch keine Ist-Zahlen hat."""
    tz_of = {e.ticker: e.tz for e in active}
    lo, hi = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=7)).isoformat()
    items = []
    for t, r in rows.items():
        cands = ([r["next"]] if r["next"] else []) + [h for h in r["history"] if h.get("eps_act") is None]
        for c in cands:
            if lo <= c["date"] <= hi and c.get("eps_act") is None:
                items.append({"ticker": t, "yahoo": r["yahoo"], "name": r["name"], "tz": tz_of.get(t, "America/New_York"),
                              "portfolio": bool(r["portfolio"]), "date": c["date"], "time": c.get("time"),
                              "ts": c.get("ts")})
    items.sort(key=lambda x: (x["date"], x["ticker"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "items": items},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def build_calendar(rows, customers, state, cfg, today) -> Calendar:
    k = cfg["kalender"]
    cal = Calendar(k["name"])
    horizon_past = (today - timedelta(days=k["vergangene_tage"])).isoformat()
    horizon_future = (today + timedelta(days=k["zukunft_tage"])).isoformat()
    title_crv = k.get("crv_im_titel", "A")

    for t, row in rows.items():
        cur = row["currency"]
        url = None
        if k.get("chart_link") and not row["extra"]:
            url = "https://www.tradingview.com/chart/?symbol=" + row["tv"].replace(":", "%3A")
        cats = [row["section"]] + (["Portfolio"] if row["portfolio"] else []) + (["Kunde"] if row["suppliers"] else [])
        head = f"{row['name']} ({t}) · {row['section']}" + (
            " · im Portfolio" if row["portfolio"] and row["section"] != "Portfolio" else "")
        tag = ("💼 " if row["portfolio"] else "")

        # ---------------- kommender Termin
        n = row["next"]
        if n and n["date"] <= horizon_future:
            s = row["tech"]
            when = TIME_LABEL.get(n["time"], "Uhrzeit offen")
            status = {True: "bestätigt", False: "vorläufig (geschätzt)"}.get(n["confirmed"], "Status unbekannt")
            L = [head, f"Termin: {dlong(n['date'])}, {when}" + (f", {berlin_time(n['ts'])}" if n.get("ts") else "")
                 + f" · {status}"]
            est = []
            if n.get("eps_est") is not None:
                est.append(f"EPS-Erwartung {num(n['eps_est'])}")
            if n.get("rev_est") is not None:
                est.append(f"Umsatz-Erwartung {big(n['rev_est'], CUR.get(cur or '', ''))}")
            if est:
                L.append(" · ".join(est))
            if row["avg_move_pct"] is not None:
                last = next((h["reaction"]["move_pct"] for h in reversed(row["history"]) if h.get("reaction")), None)
                L.append(f"Ø Kursreaktion letzte Earnings: ±{num(row['avg_move_pct'], 1)} %"
                         + (f" (zuletzt {pct(last)})" if last is not None else ""))
            L += [""] + tech_block(s, cur)
            L += customer_lines(row, rows, customers, n["date"])
            L += ["", f"Daten: {n['source']} · aktualisiert {datetime.now(BERLIN):%d.%m.%Y %H:%M}"]
            crv_v = ((s or {}).get("crv") or {}).get(title_crv)
            bits = [f"{'🏢' if row['suppliers'] else '📅'} {tag}{t} Earnings"
                    + (f" {TIME_LABEL[n['time']].replace('während des Handels', 'im Handel')}" if n["time"] else "")
                    + ("" if n["confirmed"] is not False else " (vorl.)")]
            if s and s.get("rsi") is not None:
                bits.append(f"RSI {s['rsi']:.0f}")
            if crv_v:
                bits.append(f"CRV {num(crv_v['crv'], 1)}")
            cal.add_allday(f"{row['yahoo']}-{n['date']}@earnings-kalender", date.fromisoformat(n["date"]),
                           " · ".join(bits), "\n".join(L), url, cats, k.get("erinnerung_minuten"))

        # ---------------- vergangene Termine (nachgetragen)
        for h in row["history"]:
            if h["date"] < horizon_past:
                continue
            key = f"{row['yahoo']}|{h['date']}"
            res, label, icon = result_lines(h, cur)
            if not res:
                label, icon = "Zahlen ausstehend", "⏳"
            L = [head, f"Gemeldet: {dlong(h['date'])}, {TIME_LABEL.get(h['time'], 'Uhrzeit unbekannt')}", ""]
            L += ["ERGEBNIS"] + (res or ["Noch keine Ist-Zahlen in den Daten – wird beim nächsten Lauf nachgetragen."])
            rx = h.get("reaction")
            if rx:
                L += ["", "KURSREAKTION",
                      f"{dlong(rx['day'])}: {pct(rx['move_pct'])} (Eröffnungs-Gap {pct(rx['gap_pct'])})",
                      f"Volumen am Reaktionstag: {num(rx['rel_volume'], 1)}× Ø20T"]
            pre = state["earnings"].get(key, {}).get("pre")
            if pre:
                crvs = " · ".join(f"{k2} {num(v, 1)}" for k2, v in pre.get("crv", {}).items() if v is not None)
                L += ["", f"VOR DEN EARNINGS (Stand {dshort(pre['asof'])})",
                      f"Kurs {price(pre['close'], cur)} · RSI {num(pre['rsi'], 1)} · Vol {num(pre['rel_volume'], 1)}× Ø"
                      + (f" · CRV {crvs}" if crvs else "")]
            L += customer_lines(row, rows, customers, h["date"])
            title = f"{icon} {tag}{t} {label}"
            if rx:
                title += f" · Kurs {pct(rx['move_pct'])}"
            cal.add_allday(f"{row['yahoo']}-{h['date']}@earnings-kalender", date.fromisoformat(h["date"]),
                           title, "\n".join(L), url, cats)
            state["earnings"].setdefault(key, {})["result"] = {kk: h.get(kk) for kk in ("eps_est", "eps_act", "rev_est", "rev_act")}
    return cal


def customer_lines(row, rows, customers, ref_date) -> list[str]:
    L = []
    if row["customers"]:
        L += ["", "EARNINGS DER KUNDEN / HYPERSCALER"]
        items = []
        for c in row["customers"]:
            cr = rows.get(c)
            d = None
            if cr:
                d = cr["next"]["date"] if cr["next"] else None
                last = cr["history"][-1]["date"] if cr["history"] else None
                # Bezug: der Kundentermin, der dem eigenen Termin am nächsten liegt
                if last and (d is None or abs((date.fromisoformat(last) - date.fromisoformat(ref_date)).days)
                             < abs((date.fromisoformat(d) - date.fromisoformat(ref_date)).days)):
                    d = last
            items.append((d or "9999", c, customers.get(c, {}).get("name", c)))
        for d, c, name in sorted(items):
            if d == "9999":
                L.append(f"{c} ({name}): Termin unbekannt")
            else:
                rel = "vorher" if d < ref_date else "gleicher Tag" if d == ref_date else "danach"
                L.append(f"{c} ({name}): {dlong(d)} – {rel}")
    if row["suppliers"]:
        L += ["", f"RELEVANT FÜR ZULIEFERER ({row['customer_type']})", ", ".join(row["suppliers"])]
    return L


def write_dashboard(rows, customers, path: Path):
    data = {"generated": datetime.now(timezone.utc).isoformat(), "customers": customers,
            "stocks": _compact(list(rows.values()))}
    path.write_text(json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")), encoding="utf-8")


def _compact(x):
    """Zahlen kürzen, damit die JSON-Datei klein bleibt."""
    if isinstance(x, float):
        return round(x, 2) if abs(x) >= 10 else round(x, 4)
    if isinstance(x, dict):
        return {k: _compact(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_compact(v) for v in x]
    return x


def write_report(entries, rows, path: Path):
    L = ["# Prüfbericht", "", f"Stand: {datetime.now(BERLIN):%d.%m.%Y %H:%M}", ""]
    skipped = [e for e in entries if not e.yahoo]
    nodata = [r for r in rows.values() if not r["tech"]]
    nodate = [r for r in rows.values() if not r["next"]]
    L += [f"## Nicht abrufbar ({len(skipped)})", ""] + [f"- {e.tv}: {e.note}" for e in skipped] + [""]
    L += [f"## Ohne Kursdaten ({len(nodata)})", ""] + [f"- {r['tv']} → {r['yahoo']}" for r in nodata] + [""]
    L += [f"## Ohne kommenden Earnings-Termin ({len(nodate)})", "",
          "Tipp: bei deutschen Zweitlistings in symbols_override.yaml die Heimatbörse eintragen.", ""]
    L += [f"- {r['tv']} → {r['yahoo']}" for r in nodate]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def _prune_state(state, today, cfg):
    cutoff = (today - timedelta(days=cfg["kalender"]["vergangene_tage"] + 30)).isoformat()
    state["earnings"] = {k: v for k, v in state["earnings"].items() if k.split("|")[1] >= cutoff}


def main(argv=None):
    p = argparse.ArgumentParser(description="Earnings-Kalender erzeugen")
    p.add_argument("--demo", action="store_true", help="Zufallsdaten statt Internet")
    p.add_argument("--watchlist", default="watchlist.txt")
    p.add_argument("--out", default=str(ROOT / "output"))
    p.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    p.add_argument("--watch-out", default=str(ROOT / "data" / "watch.json"))
    p.add_argument("--limit", type=int, default=0, help="nur die ersten N Werte (Test)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run(args)


if __name__ == "__main__":
    main()
