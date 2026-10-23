"""TradingView-Watchlist einlesen und TradingView-Symbole auf Yahoo-Symbole abbilden."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# TradingView-Börse -> (Yahoo-Suffix, Zeitzone der Börse)
EXCHANGES: dict[str, tuple[str, str]] = {
    "NASDAQ": ("", "America/New_York"),
    "NYSE": ("", "America/New_York"),
    "AMEX": ("", "America/New_York"),
    "NYSEARCA": ("", "America/New_York"),
    "CBOE": ("", "America/New_York"),
    "OTC": ("", "America/New_York"),
    "XETR": (".DE", "Europe/Berlin"),
    "FWB": (".F", "Europe/Berlin"),
    "TRADEGATE": (".F", "Europe/Berlin"),
    "GETTEX": (".F", "Europe/Berlin"),
    "LS": (".F", "Europe/Berlin"),
    "LSX": (".F", "Europe/Berlin"),
    "SWB": (".SG", "Europe/Berlin"),
    "VIE": (".VI", "Europe/Vienna"),
    "SIX": (".SW", "Europe/Zurich"),
    "LSE": (".L", "Europe/London"),
    "MIL": (".MI", "Europe/Rome"),
    "OMXHEX": (".HE", "Europe/Helsinki"),
    "OMXSTO": (".ST", "Europe/Stockholm"),
    "OMXCOP": (".CO", "Europe/Copenhagen"),
    "HKEX": (".HK", "Asia/Hong_Kong"),
    "SSE": (".SS", "Asia/Shanghai"),
    "SZSE": (".SZ", "Asia/Shanghai"),
    "TSE": (".T", "Asia/Tokyo"),
    "KRX": (".KS", "Asia/Seoul"),
    "ASX": (".AX", "Australia/Sydney"),
    "TSX": (".TO", "America/Toronto"),
    "TSXV": (".V", "America/Toronto"),
}

# Suffix -> Zeitzone (für Overrides, die auf eine andere Börse zeigen)
SUFFIX_TZ = {
    "": "America/New_York", ".DE": "Europe/Berlin", ".F": "Europe/Berlin", ".SG": "Europe/Berlin",
    ".VI": "Europe/Vienna", ".SW": "Europe/Zurich", ".L": "Europe/London", ".MI": "Europe/Rome",
    ".PA": "Europe/Paris", ".AS": "Europe/Amsterdam", ".HE": "Europe/Helsinki", ".ST": "Europe/Stockholm",
    ".CO": "Europe/Copenhagen", ".HK": "Asia/Hong_Kong", ".SS": "Asia/Shanghai", ".SZ": "Asia/Shanghai",
    ".T": "Asia/Tokyo", ".KS": "Asia/Seoul", ".KQ": "Asia/Seoul", ".AX": "Australia/Sydney",
    ".TO": "America/Toronto", ".V": "America/Toronto", ".TW": "Asia/Taipei",
}


@dataclass
class Entry:
    tv: str                      # z. B. "NASDAQ:NVDA"
    exchange: str                # "NASDAQ"
    ticker: str                  # "NVDA"
    section: str                 # Sektor aus der Watchlist
    portfolio: bool = False
    yahoo: str | None = None     # aufgelöstes Yahoo-Symbol
    tz: str = "America/New_York"
    note: str = ""               # Hinweis (z. B. "Override", "nicht auflösbar")
    extra: bool = False          # True = nur als Kunde ergänzt, nicht in der Watchlist
    sections: list[str] = field(default_factory=list)

    @property
    def is_us(self) -> bool:
        return bool(self.yahoo) and "." not in self.yahoo

    @property
    def label(self) -> str:
        return self.ticker


def parse_watchlist(path: Path) -> list[Entry]:
    """Liest den TradingView-Export (### = Abschnitt, sonst BÖRSE:TICKER)."""
    raw = path.read_text(encoding="utf-8-sig")
    tokens = [t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()]
    section = "Ohne Abschnitt"
    seen: dict[str, Entry] = {}
    for tok in tokens:
        if tok.startswith("###"):
            section = pretty_section(tok.lstrip("#").strip())
            continue
        if ":" in tok:
            exchange, ticker = tok.split(":", 1)
        else:
            exchange, ticker = "NASDAQ", tok
        exchange, ticker = exchange.upper(), ticker.upper()
        key = f"{exchange}:{ticker}"
        is_portfolio = section.lower() == "portfolio"
        if key in seen:
            e = seen[key]
            e.portfolio = e.portfolio or is_portfolio
            if section not in e.sections:
                e.sections.append(section)
            if e.section.lower() == "portfolio" and not is_portfolio:
                e.section = section
            continue
        seen[key] = Entry(tv=key, exchange=exchange, ticker=ticker, section=section,
                          portfolio=is_portfolio, sections=[section])
    return list(seen.values())


_WORDS = {"UND": "und", "KI": "KI", "IT-HARDWARE": "IT-Hardware", "SMR": "SMR", "LNG": "LNG",
          "AUSRUESTUNG": "Ausrüstung", "RUESTUNG": "Rüstung", "OEL": "Öl", "DUENGER": "Dünger",
          "PRUEFEN": "prüfen", "ZU": "zu", "SELTENE": "Seltene"}


def pretty_section(name: str) -> str:
    """'HALBLEITER KI UND LOGIK' -> 'Halbleiter KI und Logik'."""
    words = []
    for i, w in enumerate(name.split()):
        nice = _WORDS.get(w.upper(), w.capitalize())
        words.append(nice[0].upper() + nice[1:] if i == 0 else nice)
    return " ".join(words)


def resolve_symbols(entries: list[Entry], overrides_path: Path) -> list[Entry]:
    """Setzt das Yahoo-Symbol: zuerst Overrides, dann Börsen-Regeln."""
    overrides = {}
    if overrides_path.exists():
        overrides = {k.upper(): v for k, v in (yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}).items()}
    for e in entries:
        ov = overrides.get(e.tv)
        if ov is not None:
            if isinstance(ov, dict):
                sym = ov.get("yahoo")
                e.note = ov.get("hinweis", "Override")
            else:
                sym = ov
                e.note = "Override"
            if not sym or str(sym).lower() in ("skip", "none", "-"):
                e.yahoo = None
                e.note = "übersprungen (Override)"
                continue
            e.yahoo = str(sym).upper()
            e.tz = SUFFIX_TZ.get(_suffix(e.yahoo), "America/New_York")
            continue
        suffix, tz = EXCHANGES.get(e.exchange, (None, "America/New_York"))
        if suffix is None:
            e.note = f"Börse {e.exchange} unbekannt – bitte in symbols_override.yaml eintragen"
            continue
        if e.ticker.isdigit() and e.exchange in ("LS", "LSX", "TRADEGATE", "GETTEX", "FWB"):
            e.note = "WKN statt Ticker – bitte in symbols_override.yaml eintragen"
            continue
        ticker = e.ticker
        if e.exchange == "HKEX":
            ticker = ticker.zfill(4)
        if suffix == "":
            ticker = ticker.replace(".", "-")
        e.yahoo = ticker + suffix
        e.tz = tz
    return entries


def _suffix(sym: str) -> str:
    return sym[sym.rfind("."):] if "." in sym else ""
