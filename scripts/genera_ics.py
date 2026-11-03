#!/usr/bin/env python3
"""Genera scioperi.ics a partire dal CSV della Commissione di Garanzia (CGSSE)
pubblicato da onData su GitHub.

Uso:  python scripts/genera_ics.py            (scarica il CSV da GitHub)
      python scripts/genera_ics.py file.csv   (legge un CSV locale, per test)
"""
import csv
import hashlib
import io
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

CSV_URL = ("https://raw.githubusercontent.com/ondata/liberiamoli-tutti/"
           "main/scioperi/data/cgsse/cgsse_data.csv")
OUT_FILE = "scioperi.ics"

# Il CGSSE copre TUTTA l'Italia e tutti i servizi essenziali: senza filtro il
# calendario sarà molto pieno. Con KEYWORDS tieni solo le righe che contengono
# almeno una di queste parole (minuscole) in un qualunque campo.
# Esempio: {"veneto", "venezia", "mestre", "nazionale"}
KEYWORDS = {"veneto", "venezia", "mestre" , "treviso" , "nazionale"}

GIORNI_PASSATI = 1  # tieni anche scioperi terminati da al massimo N giorni

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/(\d{4})")


def scarica():
    ultimo = None
    for tentativo in range(1, 4):
        try:
            r = requests.get(CSV_URL, timeout=60)
            r.raise_for_status()
            return r.content.decode("utf-8-sig")
        except Exception as e:  # noqa: BLE001
            ultimo = e
            print(f"Tentativo {tentativo} fallito: {e}", file=sys.stderr)
            time.sleep(5)
    raise SystemExit(f"Impossibile scaricare il CSV: {ultimo}")


def delimitatore(testo):
    prima = testo.split("\n", 1)[0]
    return max([",", ";", "\t"], key=prima.count)


def date_in(testo):
    trovate = []
    for m in DATE_RE.finditer(testo or ""):
        try:
            if m.group(1):
                trovate.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            else:
                trovate.append(date(int(m.group(6)), int(m.group(5)), int(m.group(4))))
        except ValueError:
            pass
    return trovate


def colonne(headers, *chiavi):
    return [h for h in headers if any(k in h.lower() for k in chiavi)]


def primo(riga, headers, chiavi):
    for k in chiavi:
        for h in headers:
            if k in h.lower() and (riga.get(h) or "").strip():
                return riga[h].strip()
    return ""


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


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8-sig") as f:
            testo = f.read()
    else:
        testo = scarica()

    righe = list(csv.DictReader(io.StringIO(testo), delimiter=delimitatore(testo)))
    if not righe:
        raise SystemExit("CSV vuoto: non sovrascrivo il file esistente.")
    headers = list(righe[0].keys())

    col_inizio = colonne(headers, "inizio") or colonne(headers, "data_sciopero", "data sciopero")
    col_fine = colonne(headers, "fine")
    col_stato = colonne(headers, "stato")
    col_id = [h for h in headers if h.lower() in ("id", "id_sciopero", "codice", "id_evento")]

    if not col_inizio:
        print("Non trovo la colonna con la data di inizio. Colonne presenti:", file=sys.stderr)
        for h in headers:
            print(f"  - {h}", file=sys.stderr)
        raise SystemExit(1)

    limite = date.today() - timedelta(days=GIORNI_PASSATI)
    eventi = {}

    for riga in righe:
        # date
        inizio = fine_alt = None
        for c in col_inizio:
            ds = date_in(riga.get(c))
            if ds:
                inizio, fine_alt = ds[0], ds[-1]
                break
        if not inizio:
            continue
        fine = None
        for c in col_fine:
            ds = date_in(riga.get(c))
            if ds:
                fine = ds[-1]
                break
        fine = fine or fine_alt or inizio
        if fine < inizio:
            fine = inizio
        if fine < limite:
            continue

        # scioperi revocati/annullati
        if any(("revoc" in (riga.get(c) or "").lower() or "annull" in (riga.get(c) or "").lower())
               for c in col_stato):
            continue

        # filtro per parole chiave
        if KEYWORDS:
            tutto = " ".join((v or "") for v in riga.values()).lower()
            if not any(k in tutto for k in KEYWORDS):
                continue

        settore = primo(riga, headers, ["settore"])
        luogo = primo(riga, headers, ["regione", "provincia", "localit", "comune", "rilevanza"])
        categoria = primo(riga, headers, ["categoria", "azienda", "denominazione", "descrizione", "sindacat"])
        parti = [p for p in (settore, luogo, categoria) if p]
        summary = "Sciopero" + (": " + " - ".join(parti) if parti else "")
        if len(summary) > 110:
            summary = summary[:107] + "..."

        descr = []
        for h in headers:
            v = (riga.get(h) or "").strip()
            if v:
                descr.append(f"{h}: {v[:300]}")

        if col_id and (riga.get(col_id[0]) or "").strip():
            chiave = riga[col_id[0]].strip()
        else:
            chiave = hashlib.sha1(f"{inizio}|{fine}|{summary}".encode()).hexdigest()[:16]
        uid = f"{chiave}@scioperi-cgsse"

        eventi.setdefault(uid, {
            "uid": uid, "inizio": inizio, "fine": fine, "summary": summary,
            "description": "\n".join(descr) + "\n\nFonte: https://www.cgsse.it/calendario-scioperi",
        })

    if not eventi:
        raise SystemExit("Nessuno sciopero da pubblicare (controlla KEYWORDS e le date): non sovrascrivo il file.")

    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//scioperi-cgsse//IT",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Scioperi",
        "X-WR-TIMEZONE:Europe/Rome", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for e in sorted(eventi.values(), key=lambda x: (x["inizio"], x["uid"])):
        stamp = datetime(e["inizio"].year, e["inizio"].month, e["inizio"].day,
                         tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['inizio'].strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(e['fine'] + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{esc(e['summary'])}",
            f"DESCRIPTION:{esc(e['description'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")

    with open(OUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(r) for r in out) + "\r\n")
    print(f"Scritti {len(eventi)} eventi in {OUT_FILE}")


if __name__ == "__main__":
    main()
