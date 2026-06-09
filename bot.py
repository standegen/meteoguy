#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MétéoGuy — Bot Telegram + planificateur (process unique, pour Docker/VPS).

- Écoute les commandes Telegram (/meteo, /orages, /jour, /semaine, /elnino, /alerte).
- Planifie en tâche de fond : alerte orages/grêle toutes les 30 min, briefing le lundi.
- N'obéit qu'au propriétaire (TELEGRAM_CHAT_ID).
"""
import os, sys, time, json, threading, datetime
import urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meteoguy as m
import briefing_hebdo

OWNER = str(os.environ.get("TELEGRAM_CHAT_ID") or m.load_config().get("chat_id") or "")
ALERTE_INTERVAL = 30 * 60          # 30 min
BRIEFING_HHMM = (7, 30)            # lundi 07:30 (heure du conteneur)

AIDE = (
    "🌤️ <b>MétéoGuy — ton expert météo</b>\n"
    "Zones suivies : <b>Sablons (38550)</b>, Grury (71760), Lapeyrouse-Mornay (26210).\n\n"
    "<b>Commandes :</b>\n"
    "• /meteo — météo du jour à Sablons + créneaux de pluie + orages\n"
    "• /jour <code>quand</code> — météo d'un jour : <code>demain</code>, <code>lundi</code>, "
    "<code>lundi prochain</code>, <code>weekend</code>, <code>+3</code> ou <code>2026-06-13</code>\n"
    "• /orages <code>[quand]</code> — analyse orages complète des 3 zones (données live)\n"
    "• /radar — image radar des précipitations en temps réel\n"
    "• /semaine — briefing 7 jours + El Niño (analyse Claude)\n"
    "• /elnino — point El Niño / ENSO\n"
    "• /alerte — forcer un contrôle orages/grêle maintenant\n"
    "• /aide — ce message\n\n"
    "<i>Automatique : surveillance grêle toutes les 30 min + briefing chaque lundi matin.</i>"
)


# --------------------------------------------------------------------------- #
# Telegram bas niveau
# --------------------------------------------------------------------------- #
def api(method, params=None, timeout=60):
    token = m.load_token()
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return json.load(r)


def send(text):
    return m.send_telegram(text)


def send_photo(png, caption):
    token = m.load_token()
    boundary = "----MeteoGuyBoundary7c3f"

    def field(name, value):
        return ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, name, value)).encode("utf-8")

    body = field("chat_id", OWNER) + field("caption", caption) + field("parse_mode", "HTML")
    body += ("--%s\r\nContent-Disposition: form-data; name=\"photo\"; "
             "filename=\"radar.png\"\r\nContent-Type: image/png\r\n\r\n" % boundary).encode()
    body += png + b"\r\n" + ("--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendPhoto" % token, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get("ok", False)


def parse_when(args):
    """Date depuis des mots relatifs FR (ou aujourd'hui par défaut)."""
    return m.parse_fr_date(" ".join(args)) if args else datetime.date.today()


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def handle(text):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0]   # retire @bot
    args = parts[1:]

    if cmd in ("/start", "/aide", "/help"):
        send(AIDE)
    elif cmd == "/meteo":
        send(m.render_jour(datetime.date.today()))
    elif cmd == "/jour":
        day = parse_when(args)
        send(m.render_jour(day) if day else
             "❓ Date non comprise. Essaie : demain, lundi, lundi prochain, weekend, +3, 2026-06-13.")
    elif cmd == "/orages":
        day = parse_when(args)
        send(m.render_orages_report(day) if day else
             "❓ Date non comprise. Essaie : demain, lundi, weekend, +3, 2026-06-13.")
    elif cmd == "/radar":
        send("📡 Génération du radar…")
        try:
            import radar
            las = [la for la, lo in m.ZONES.values()]
            los = [lo for la, lo in m.ZONES.values()]
            center = (sum(las) / len(las), sum(los) / len(los))
            png, ts = radar.radar_png(center, zones=m.ZONES, zoom=8)
            send_photo(png, radar.radar_caption(ts))
        except Exception as e:
            send("⚠️ Radar indisponible : %s" % e)
    elif cmd == "/elnino":
        send(m.render_elnino())
    elif cmd == "/alerte":
        msg, _ = m.render_alertes()
        send(msg if msg else "✅ RAS — aucun orage/grêle dans les 2 h sur les 3 zones.")
    elif cmd in ("/semaine", "/briefing"):
        send("⏳ Génération du briefing (analyse en cours)…")
        try:
            data = briefing_hebdo.build_data()
            msg, brain = briefing_hebdo.generate(data)
            send(msg if msg else m.render_briefing())
        except Exception as e:
            send("⚠️ Erreur briefing : %s" % e)
    else:
        send("Commande inconnue. Tape /aide pour la liste.")


# --------------------------------------------------------------------------- #
# Boucle d'écoute (long polling)
# --------------------------------------------------------------------------- #
def poll_loop():
    offset = None
    while True:
        try:
            params = {"timeout": 50}
            if offset:
                params["offset"] = offset
            res = api("getUpdates", params, timeout=60).get("result", [])
            for u in res:
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text:
                    continue
                if OWNER and chat != OWNER:
                    continue   # ignore tout le monde sauf le propriétaire
                try:
                    handle(text)
                except Exception as e:
                    send("⚠️ Erreur : %s" % e)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)


# --------------------------------------------------------------------------- #
# Planificateur (alerte 30 min + briefing lundi)
# --------------------------------------------------------------------------- #
def scheduler_loop():
    last_alerte = 0
    last_briefing = None
    while True:
        now = datetime.datetime.now()
        try:
            if time.time() - last_alerte >= ALERTE_INTERVAL:
                last_alerte = time.time()
                msg, new_keys = m.render_alertes()
                if msg and send(msg):
                    m.save_state(new_keys)
                m.log_line("%s %s" % (now.strftime("%Y-%m-%d %H:%M"),
                                      "ALERTE envoyée" if msg else "RAS"))
            if (now.weekday() == 0 and (now.hour, now.minute) >= BRIEFING_HHMM
                    and last_briefing != now.date()):
                last_briefing = now.date()
                data = briefing_hebdo.build_data()
                bmsg, brain = briefing_hebdo.generate(data)
                send(bmsg if bmsg else m.render_briefing())
                m.log_line("%s BRIEFING hebdo (%s)" % (now.strftime("%Y-%m-%d %H:%M"), brain))
        except Exception as e:
            print("scheduler error:", e, flush=True)
        time.sleep(60)


def main():
    if not m.load_token() or not OWNER:
        print("ERREUR : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant.", flush=True)
        sys.exit(1)
    # Message de démarrage : rapport + commandes
    try:
        send("🟢 <b>MétéoGuy est en ligne</b> (hébergé sur VPS, 24/7).\n\n" + AIDE)
        send(m.render_jour(datetime.date.today()))
    except Exception as e:
        print("startup send error:", e, flush=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("Bot démarré.", flush=True)
    poll_loop()


if __name__ == "__main__":
    main()
