from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
from openpyxl import Workbook
import requests
import sqlite3
import io
import os

app = Flask(__name__)
CORS(app)

BOT_TOKEN = "DEIN_BOT_TOKEN"


@app.route("/")
def startseite():
    return "Server läuft."


@app.route("/status", methods=["POST"])
def status():
    daten = request.json
    mitarbeiter_id = daten.get("mitarbeiter_id", "").strip()

    if not mitarbeiter_id:
        return jsonify({"status": "fehler", "meldung": "Mitarbeiter-ID fehlt"})

    conn = sqlite3.connect("baustelle.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT aktion
        FROM anwesenheit
        WHERE mitarbeiter_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (mitarbeiter_id,))

    result = cursor.fetchone()
    conn.close()

    if result and result[0] == "Start":
        naechste_aktion = "Feierabend"
    else:
        naechste_aktion = "Start"

    return jsonify({"status": "ok", "naechste_aktion": naechste_aktion})


@app.route("/scan", methods=["POST"])
def scan():
    daten = request.json

    aktion = daten.get("aktion", "").strip()
    qr_code = daten.get("qr_code", "").strip()
    mitarbeiter_id = daten.get("mitarbeiter_id", "").strip()
    telegram_id = str(daten.get("telegram_id", "")).strip()

    # -------------------------------------------------
    # Arbeitsplatz Scan
    # -------------------------------------------------
    if aktion == "Arbeitsplatz":

        if not qr_code.startswith("DKL_"):
            return jsonify({
                "status": "fehler",
                "meldung": "Bitte einen Arbeitsplatz QR-Code scannen"
            })

        conn = sqlite3.connect("baustelle.db")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT aktion
            FROM anwesenheit
            WHERE mitarbeiter_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (mitarbeiter_id,))

        result = cursor.fetchone()

        if not result or result[0] != "Start":
            conn.close()
            return jsonify({
                "status": "fehler",
                "meldung": "Arbeitsplatz nur nach Start möglich"
            })

        arbeitsplatz = qr_code.replace("DKL_", "").strip()
        datum = datetime.now().strftime("%d.%m.%Y")
        uhrzeit = datetime.now().strftime("%H:%M:%S")

        cursor.execute("""
            INSERT INTO arbeitsplatz (datum, uhrzeit, mitarbeiter_id, telegram_id, arbeitsplatz)
            VALUES (?, ?, ?, ?, ?)
        """, (datum, uhrzeit, mitarbeiter_id, telegram_id, arbeitsplatz))

        conn.commit()
        conn.close()

        nachricht = f"Aktueller Arbeitsort ab {uhrzeit} Uhr:\n{arbeitsplatz}"

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": telegram_id,
                "text": nachricht
            }
        )

        print("Arbeitsplatz gespeichert:", arbeitsplatz, mitarbeiter_id)

        return jsonify({"status": "ok"})

    # -------------------------------------------------
    # Arbeitszeit Scan
    # -------------------------------------------------
    if "_" in qr_code:
        return jsonify({
            "status": "fehler",
            "meldung": "Bitte Baustellen QR-Code scannen"
        })

    try:
        teile = qr_code.split("|")
        baustelle = teile[0]
        zeitfenster = int(teile[1])
    except:
        return jsonify({"status": "fehler", "meldung": "QR-Code ungültig"})

    jetzt = int(datetime.now().timestamp())

    if abs(jetzt - zeitfenster) > 60:
        return jsonify({"status": "fehler", "meldung": "QR-Code abgelaufen"})

    datum = datetime.now().strftime("%d.%m.%Y")
    uhrzeit = datetime.now().strftime("%H:%M:%S")

    conn = sqlite3.connect("baustelle.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT aktion
        FROM anwesenheit
        WHERE mitarbeiter_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (mitarbeiter_id,))

    result = cursor.fetchone()
    letzte_aktion = result[0] if result else None

    # Sicherheitslogik
    if aktion == "Start" and letzte_aktion == "Start":
        conn.close()
        return jsonify({
            "status": "fehler",
            "meldung": "Du bist bereits eingecheckt"
        })

    if aktion == "Feierabend" and letzte_aktion != "Start":
        conn.close()
        return jsonify({
            "status": "fehler",
            "meldung": "Feierabend ohne Start nicht möglich"
        })

    cursor.execute("""
        INSERT INTO anwesenheit (datum, uhrzeit, mitarbeiter_id, telegram_id, aktion, baustelle)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datum, uhrzeit, mitarbeiter_id, telegram_id, aktion, baustelle))

    conn.commit()
    conn.close()

    nachricht = f"{aktion} in {baustelle} am {datum} um {uhrzeit} Uhr gespeichert."

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": telegram_id,
            "text": nachricht
        }
    )

    print("Arbeitszeit gespeichert:", aktion, baustelle, mitarbeiter_id)

    return jsonify({"status": "ok"})


@app.route("/debug")
def debug():
    conn = sqlite3.connect("baustelle.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT datum, uhrzeit, mitarbeiter_id, telegram_id, aktion, baustelle
        FROM anwesenheit
        ORDER BY id DESC
        LIMIT 20
    """)

    rows = cursor.fetchall()
    conn.close()

    return jsonify(rows)


@app.route("/export")
def export_excel():
    conn = sqlite3.connect("baustelle.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT datum, uhrzeit, mitarbeiter_id, telegram_id, aktion, baustelle
        FROM anwesenheit
        ORDER BY id
    """)

    rows = cursor.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Anwesenheit"

    ws.append(["Datum", "Uhrzeit", "Mitarbeiter ID", "Telegram ID", "Aktion", "Baustelle"])

    for row in rows:
        ws.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    zeitstempel = datetime.now().strftime("%Y-%m-%d_%H-%M")
    dateiname = f"Anwesenheit_DKL_{zeitstempel}.xlsx"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=dateiname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
