#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MétéoGuy — Bot Telegram (boutons/menus) + planificateur. Process unique (Docker/VPS).

- Commandes texte ET boutons inline (routeur d'actions partagé run_action).
- Menu natif "/" (setMyCommands), menu à boutons (/menu).
- Planificateur : alerte orages/grêle (15 min en saison, 30 min sinon) + briefing lundi.
- Heartbeat optionnel (dead-man switch) via HEARTBEAT_URL.
- N'obéit qu'au propriétaire (TELEGRAM_CHAT_ID).
"""
import os, sys, time, json, threading, datetime
import urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meteoguy as m
import briefing_hebdo

OWNER = str(os.environ.get("TELEGRAM_CHAT_ID") or m.load_config().get("chat_id") or "")
HEARTBEAT_URL = os.environ.get("HEARTBEAT_URL")   # ex: https://hc-ping.com/<uuid>
BRIEFING_HHMM = (7, 30)


def alerte_interval():
    """15 min en saison convective (mai-sept), 30 min sinon."""
    return 15 * 60 if 5 <= datetime.date.today().month <= 9 else 30 * 60


AIDE = (
    "🌤️ <b>MétéoGuy — ton expert météo</b>\n"
    "Zones : <b>Sablons (38550)</b>, Grury (71760), Lapeyrouse-Mornay (26210).\n\n"
    "<b>Commandes :</b>\n"
    "• /menu — menu à boutons\n"
    "• /meteo — météo du jour + créneaux de pluie + orages\n"
    "• /jour <code>quand</code> — <code>demain</code>, <code>lundi</code>, "
    "<code>lundi prochain</code>, <code>weekend</code>, <code>+3</code>, <code>2026-06-13</code>\n"
    "• /orages <code>[quand]</code> — analyse orages des 3 zones (données live)\n"
    "• /radar — image radar en temps réel\n"
    "• /semaine — briefing 7 jours + El Niño\n"
    "• /elnino — point El Niño / ENSO\n"
    "• /alerte — contrôle orages/grêle immédiat\n\n"
    "<i>Auto : surveillance grêle (15-30 min) + briefing chaque lundi.</i>"
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


def send_kb(text, keyboard, chat_id=None):
    """Message HTML + clavier inline. keyboard = liste de rangées de (label, data)."""
    rows = [[{"text": lbl, "callback_data": data} for (lbl, data) in row] for row in keyboard]
    try:
        return api("sendMessage", {
            "chat_id": chat_id or OWNER, "text": text, "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": rows})}).get("ok", False)
    except Exception as e:
        print("send_kb error:", e, flush=True)
        return False


def answer_callback(cb_id, text=None):
    params = {"callback_query_id": cb_id}
    if text:
        params["text"] = text
    try:
        api("answerCallbackQuery", params)
    except Exception:
        pass


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


# --------------------------------------------------------------------------- #
# Menus & commandes natives
# --------------------------------------------------------------------------- #
def main_menu_kb():
    return [
        [("🌤️ Aujourd'hui", "act:meteo"), ("⛈️ Orages", "act:orages")],
        [("📡 Radar", "act:radar"), ("📅 Semaine", "act:semaine")],
        [("🌊 El Niño", "act:elnino"), ("🚨 Contrôle alerte", "act:alerte")],
        [("📆 Choisir un jour", "nav:jourmenu")],
    ]


def date_menu_kb(action):
    return [
        [("Aujourd'hui", "day:%s:0" % action), ("Demain", "day:%s:1" % action)],
        [("Après-demain", "day:%s:2" % action), ("Week-end", "day:%s:we" % action)],
        [("Lundi", "day:%s:lundi" % action), ("⬅️ Retour", "nav:main")],
    ]


def resolve_when(quand):
    table = {"0": "aujourd'hui", "1": "demain", "2": "après-demain",
             "we": "weekend", "lundi": "lundi"}
    return m.parse_fr_date(table.get(quand, quand)) or datetime.date.today()


def set_my_commands():
    cmds = [
        {"command": "menu", "description": "Menu à boutons"},
        {"command": "meteo", "description": "Météo du jour à Sablons"},
        {"command": "jour", "description": "Météo d'un jour (demain, lundi, +3…)"},
        {"command": "orages", "description": "Analyse orages des 3 zones"},
        {"command": "radar", "description": "Image radar temps réel"},
        {"command": "semaine", "description": "Briefing 7 jours + El Niño"},
        {"command": "elnino", "description": "Point El Niño / ENSO"},
        {"command": "alerte", "description": "Contrôle orages/grêle immédiat"},
        {"command": "aide", "description": "Aide"},
    ]
    api("setMyCommands", {"commands": json.dumps(cmds)})


# --------------------------------------------------------------------------- #
# Routeur d'actions (partagé entre commandes texte et boutons)
# --------------------------------------------------------------------------- #
def run_action(action, day=None):
    day = day or datetime.date.today()
    if action == "meteo":
        send(m.render_jour(datetime.date.today()))
    elif action == "jour":
        send(m.render_jour(day))
    elif action == "orages":
        send(m.render_orages_report(day))
    elif action == "radar":
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
    elif action == "elnino":
        send(m.render_elnino())
    elif action == "alerte":
        msg, _ = m.render_alertes()
        send(msg if msg else "✅ RAS — aucun orage/grêle dans les 2 h sur les 3 zones.")
    elif action == "semaine":
        send("⏳ Génération du briefing (analyse en cours)…")
        try:
            data = briefing_hebdo.build_data()
            msg, brain = briefing_hebdo.generate(data)
            send(msg if msg else m.render_briefing())
        except Exception as e:
            send("⚠️ Erreur briefing : %s" % e)
    elif action == "menu":
        send_kb("📋 <b>Menu MétéoGuy</b> — choisis :", main_menu_kb())
    elif action == "aide":
        send(AIDE)


def parse_when(args):
    return m.parse_fr_date(" ".join(args)) if args else datetime.date.today()


# --------------------------------------------------------------------------- #
# Dispatch commandes texte / clics boutons
# --------------------------------------------------------------------------- #
def handle(text):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0].lstrip("/")
    args = parts[1:]
    if cmd in ("start", "aide", "help"):
        run_action("aide")
        run_action("menu")
    elif cmd in ("menu", "meteo", "radar", "elnino", "alerte"):
        run_action(cmd)
    elif cmd in ("semaine", "briefing"):
        run_action("semaine")
    elif cmd in ("jour", "orages"):
        day = parse_when(args)
        if not day:
            send("❓ Date non comprise. Essaie : demain, lundi, weekend, +3, 2026-06-13.")
        else:
            run_action(cmd, day)
    else:
        send("Commande inconnue. Tape /menu ou /aide.")


def handle_callback(cb):
    answer_callback(cb["id"])
    data = cb.get("data", "")
    if data == "nav:main":
        send_kb("📋 <b>Menu MétéoGuy</b> — choisis :", main_menu_kb())
    elif data == "nav:jourmenu":
        send_kb("📆 <b>Quel jour ?</b>", date_menu_kb("jour"))
    elif data.startswith("act:"):
        run_action(data.split(":", 1)[1])
    elif data.startswith("day:"):
        _, action, quand = data.split(":", 2)
        run_action(action, resolve_when(quand))


# --------------------------------------------------------------------------- #
# Boucle d'écoute (messages + callbacks)
# --------------------------------------------------------------------------- #
def poll_loop():
    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])}
            if offset:
                params["offset"] = offset
            for u in api("getUpdates", params, timeout=60).get("result", []):
                offset = u["update_id"] + 1
                cb = u.get("callback_query")
                if cb:
                    if OWNER and str(cb.get("from", {}).get("id", "")) != OWNER:
                        continue
                    try:
                        handle_callback(cb)
                    except Exception as e:
                        send("⚠️ Erreur : %s" % e)
                    continue
                msg = u.get("message") or u.get("edited_message") or {}
                if OWNER and str(msg.get("chat", {}).get("id", "")) != OWNER:
                    continue
                text = msg.get("text", "")
                if text:
                    try:
                        handle(text)
                    except Exception as e:
                        send("⚠️ Erreur : %s" % e)
        except Exception as e:
            print("poll error:", e, flush=True)
            time.sleep(5)


# --------------------------------------------------------------------------- #
# Planificateur (alerte + briefing) + heartbeat
# --------------------------------------------------------------------------- #
def scheduler_loop():
    last_alerte, last_briefing = 0, None
    while True:
        now = datetime.datetime.now()
        try:
            if time.time() - last_alerte >= alerte_interval():
                last_alerte = time.time()
                msg, new_keys = m.render_alertes()
                if msg and send(msg):
                    m.save_state(new_keys)
                m.log_line("%s %s" % (now.strftime("%Y-%m-%d %H:%M"),
                                      "ALERTE/TECH envoyée" if msg else "RAS"))
                if HEARTBEAT_URL:        # dead-man switch : ping après chaque passe réussie
                    try:
                        urllib.request.urlopen(HEARTBEAT_URL, timeout=10)
                    except Exception:
                        pass
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
    try:
        set_my_commands()
    except Exception as e:
        print("setMyCommands error:", e, flush=True)
    try:
        send_kb("🟢 <b>MétéoGuy est en ligne</b> (VPS, 24/7).\nTape /menu à tout moment.",
                main_menu_kb())
        send(m.render_jour(datetime.date.today()))
    except Exception as e:
        print("startup send error:", e, flush=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("Bot démarré.", flush=True)
    poll_loop()


if __name__ == "__main__":
    main()
