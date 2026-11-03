#!/usr/bin/env python3
"""Scarica il feed RSS degli scioperi del MIT e genera scioperi.ics.

Uso:  python scripts/genera_ics.py            (scarica dal MIT)
      python scripts/genera_ics.py feed.xml   (legge da file locale, per test)
"""
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

FEED_URL = "http://scioperi.mit.gov.it/mit2/public/scioperi/rss"
OUT_FILE = "scioperi.ics"

# Facoltativo: tieni solo gli scioperi nazionali + quelli di queste regioni.
# None = tieni tutto. Esempio: {"Veneto"}
REGIONI = None


def scarica_feed():
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    ultimo_errore = None
    for tentativo in range(1, 4):
        try:
            r = requests.get(FEED_URL, headers=headers, timeout=30)
            r.raise_for_status()
            if b"<rss" not in r.content:
                raise RuntimeError("La risposta non sembra un feed RSS")
            return r.content
        except Exception as e:  # noqa: BLE001
            ultimo_errore = e
            print(f"Tentativo {tentativo} fallito: {e}", file=sys.stderr)
            time.sleep(5)
    raise SystemExit(f"Impossibile scaricare il feed: {ultimo_errore}")


def parse_data(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def parse_item(item):
    titolo = item.findtext("title", "")
    descr = item.findtext("description", "")
    guid = item.findtext("guid", "")

    campi = {}
    for parte in descr.split("<br/>"):
        if ": " in parte:
            k, v = parte.split(": ", 1)
            campi[k.strip().lower()] = v.strip()

    m = re.search(r"Data inizio:\s*(\d{2}/\d{2}/\d{4})", titolo)
    if not m:
        return None
    inizio = parse_data(m.group(1))
    fine = parse_data(campi["data fine"]) if "data fine" in campi else inizio
    ricezione = parse_data(campi["data ricezione"]) if "data ricezione" in campi else inizio

    settore = campi.get("settore", "")
    rilevanza = campi.get("rilevanza", "")
    regione = campi.get("regione", "")
    provincia = campi.get("provincia", "")
    categoria = campi.get("categoria interessata", "")

    if REGIONI is not None and rilevanza.lower() != "nazionale" and regione not in REGIONI:
        return None

    if rilevanza.lower() == "nazionale":
        luogo = "Nazionale"
    elif provincia and provincia.lower() != "tutte":
        luogo = provincia
    else:
        luogo = regione

    summary = f"Sciopero {settore} - {luogo} - {categoria}"
    if len(summary) > 110:
        summary = summary[:107] + "..."

    righe = [
        f"Modalità: {campi.get('modalità', 'n.d.')}",
        f"Settore: {settore}",
        f"Rilevanza: {rilevanza}",
        f"Regione: {regione}",
        f"Provincia: {provincia}",
        f"Sindacati: {campi.get('sindacati', '')}",
        f"Categoria: {categoria}",
        f"Proclamazione: {campi.get('data proclamazione', '')}",
        "",
        "Fonte: http://scioperi.mit.gov.it/mit2/public/scioperi",
    ]

    return {
        "uid": f"{guid.rstrip('/').split('/')[-1] or titolo}@scioperi-mit",
        "inizio": inizio,
        "fine": fine,
        "stamp": datetime(ricezione.year, ricezione.month, ricezione.day, tzinfo=timezone.utc),
        "summary": summary,
        "description": "\n".join(righe),
    }


def esc(t):
    return (t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
             .replace("\r", "").replace("\n", "\\n"))


def fold(line):
    b = line.encode("utf-8")
    parti, limite = [], 75
    while len(b) > limite:
        cut = limite
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        parti.append(b[:cut].decode("utf-8"))
        b = b[cut:]
        limite = 74
    parti.append(b.decode("utf-8"))
    return "\r\n ".join(parti)


def genera_ics(eventi):
    righe = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//scioperi-mit//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Scioperi MIT",
        "X-WR-TIMEZONE:Europe/Rome",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for e in eventi:
        righe += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{e['stamp'].strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{e['inizio'].strftime('%Y%m%d')}",
            # in iCalendar la data di fine di un evento "tutto il giorno" è esclusiva
            f"DTEND;VALUE=DATE:{(e['fine'] + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{esc(e['summary'])}",
            f"DESCRIPTION:{esc(e['description'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    righe.append("END:VCALENDAR")
    return "\r\n".join(fold(r) for r in righe) + "\r\n"


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as f:
            contenuto = f.read()
    else:
        contenuto = scarica_feed()

    root = ET.fromstring(contenuto)
    eventi = [p for p in (parse_item(i) for i in root.iter("item")) if p]
    if not eventi:
        raise SystemExit("Nessuno sciopero trovato nel feed: non sovrascrivo il file esistente.")

    eventi.sort(key=lambda e: (e["inizio"], e["uid"]))
    with open(OUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(genera_ics(eventi))
    print(f"Scritti {len(eventi)} eventi in {OUT_FILE}")


if __name__ == "__main__":
    main()
