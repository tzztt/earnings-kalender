"""Technische Kennzahlen, Kursmarken, CRV-Varianten und Earnings-Reaktionen."""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- Indikatoren

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(avg_up.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def swing_levels(df: pd.DataFrame, window: int, cluster_pct: float, close: float):
    """Swing-Hochs über dem Kurs (Widerstände) und Swing-Tiefs darunter (Unterstützungen)."""
    span = 2 * window + 1
    hi, lo = df["High"], df["Low"]
    piv_hi = hi[hi == hi.rolling(span, center=True).max()].dropna()
    piv_lo = lo[lo == lo.rolling(span, center=True).min()].dropna()
    res = _cluster(sorted(v for v in piv_hi.values if v > close * 1.002), cluster_pct)
    sup = _cluster(sorted((v for v in piv_lo.values if v < close * 0.998), reverse=True), cluster_pct)
    return res, sup


def _cluster(levels, pct):
    out = []
    for lv in levels:
        if not out or abs(lv - out[-1]) / out[-1] * 100 > pct:
            out.append(float(lv))
    return out


# ---------------------------------------------------------------- Snapshot

def technical_snapshot(df: pd.DataFrame, cfg: dict) -> dict | None:
    """Alle Kennzahlen zum letzten Handelstag. df: OHLCV mit Datumsindex."""
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return None
    t = cfg["technik"]
    close = float(df["Close"].iloc[-1])
    last_year = df.iloc[-252:]
    r = rsi(df["Close"], t["rsi_periode"])
    a = atr(df, t["atr_periode"])
    n_vol = t["volumen_schnitt_tage"]
    vol = float(df["Volume"].iloc[-1]) if not pd.isna(df["Volume"].iloc[-1]) else None
    vol_avg = float(df["Volume"].iloc[-n_vol - 1:-1].mean()) if len(df) > n_vol else None

    def sma(n):
        return float(df["Close"].rolling(n).mean().iloc[-1]) if len(df) >= n else None

    res, sup = swing_levels(last_year, t["swing_fenster"], t["marken_cluster_prozent"], close)
    high52, low52 = float(last_year["High"].max()), float(last_year["Low"].min())
    prev = float(df["Close"].iloc[-2])
    snap = {
        "asof": _d(df.index[-1]).isoformat(),
        "close": close,
        "change_pct": (close / prev - 1) * 100 if prev else None,
        "rsi": _f(r.iloc[-1]),
        "atr": _f(a.iloc[-1]),
        "atr_pct": _f(a.iloc[-1] / close * 100),
        "sma20": sma(20), "sma50": sma(50), "sma200": sma(200),
        "high52": high52, "low52": low52,
        "dist_high52_pct": (close / high52 - 1) * 100,
        "volume": vol, "volume_avg": vol_avg,
        "rel_volume": (vol / vol_avg) if vol and vol_avg else None,
        "resistances": res[:3],
        "supports": sup[:3],
        "spark": [round(float(x), 4) for x in df["Close"].iloc[-60:].values],
    }
    snap["crv"] = crv_variants(snap, cfg["crv"])
    return snap


def crv_variants(s: dict, c: dict) -> dict:
    """Drei CRV-Varianten. Alle beziehen sich auf eine Long-Position."""
    close, atr_v = s["close"], s["atr"]
    if not atr_v:
        return {}

    def target_from(entry):
        cands = [r for r in s["resistances"] if r >= entry + c["ziel_min_atr"] * atr_v]
        if cands:
            return cands[0], "nächster Widerstand"
        if s["high52"] >= entry + c["ziel_min_atr"] * atr_v:
            return s["high52"], "52W-Hoch"
        return entry + c["ath_ziel_atr"] * atr_v, f"Projektion {c['ath_ziel_atr']:g}×ATR (kein Widerstand darüber)"

    def pack(entry, stop, target, tnote, note):
        risk = entry - stop
        if risk <= 0:
            return None
        return {
            "entry": entry, "stop": stop, "target": target,
            "stop_pct": (stop / entry - 1) * 100, "target_pct": (target / entry - 1) * 100,
            "crv": (target - entry) / risk, "target_note": tnote, "note": note,
        }

    out = {}
    tgt, tnote = target_from(close)

    # A – Struktur: Stop unter dem nächsten Swing-Tief
    if s["supports"]:
        stop = s["supports"][0] * (1 - c["a_stop_puffer_prozent"] / 100)
        out["A"] = pack(close, stop, tgt, tnote, "Stop unter letztem Swing-Tief")
    # B – Volatilität: Stop = Kurs − k × ATR
    out["B"] = pack(close, close - c["b_atr_faktor"] * atr_v, tgt, tnote, f"Stop {c['b_atr_faktor']:g}×ATR")
    # C – Pullback: Einstieg an der höchsten Unterstützung unter dem Kurs
    cands = [(v, n) for v, n in ((s["sma20"], "SMA20"), (s["sma50"], "SMA50"),
                                 (s["supports"][0] if s["supports"] else None, "Swing-Tief"))
             if v and close * (1 - c["c_max_abstand_prozent"] / 100) <= v < close - 0.3 * atr_v]
    if cands:
        entry, name = max(cands)
        t2, tn2 = target_from(entry)
        r = pack(entry, entry - c["c_atr_faktor"] * atr_v, t2, tn2, f"Einstieg am {name}")
        if r:
            r["entry_dist_pct"] = (entry / close - 1) * 100
        out["C"] = r
    return {k: _round_dict(v) for k, v in out.items() if v}


# ---------------------------------------------------------------- Earnings-Reaktion

def earnings_reaction(df: pd.DataFrame, d: date, timing: str | None) -> dict | None:
    """Kursreaktion: Schluss vor der Meldung -> Schluss der ersten Sitzung danach."""
    if df is None or df.empty:
        return None
    idx = pd.Index([_d(x) for x in df.index])
    closes = pd.Series(df["Close"].values, index=idx)
    vols = pd.Series(df["Volume"].values, index=idx)
    opens = pd.Series(df["Open"].values, index=idx)
    if timing == "amc":
        before = closes[closes.index <= d]
        after_idx = closes.index[closes.index > d]
    else:  # bmo, während Handel oder unbekannt
        before = closes[closes.index < d]
        after_idx = closes.index[closes.index >= d]
    if before.empty or len(after_idx) == 0:
        return None
    day = after_idx[0]
    c0, c1 = float(before.iloc[-1]), float(closes[day])
    vol_avg = float(vols[vols.index < day].iloc[-20:].mean())
    return {
        "day": day.isoformat(),
        "close_before": c0,
        "move_pct": (c1 / c0 - 1) * 100,
        "gap_pct": (float(opens[day]) / c0 - 1) * 100,
        "rel_volume": float(vols[day]) / vol_avg if vol_avg else None,
    }


# ---------------------------------------------------------------- Hilfen

def _d(x) -> date:
    return x.date() if hasattr(x, "date") else x


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _round_dict(d):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}
