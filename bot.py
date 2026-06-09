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
DAILY_HHMM = (6, 50)          # message quotidien (pluie/temp/habillage enfant)
FOUDRE_RAYON_KM = 50.0
_foudre_last = {}             # throttle par zone


def alerte_interval():
    """15 min en saison convective (mai-sept), 30 min sinon."""
    return 15 * 60 if 5 <= datetime.date.today().month <= 9 else 30 * 60


def on_impact(point, lat, lon, ts, dist):
    """Callback foudre : alerte + carte (throttle 15 min par zone)."""
    if time.time() - _foudre_last.get(point["nom"], 0) < 900:
        return
    _foudre_last[point["nom"]] = time.time()
    heure = datetime.datetime.fromtimestamp(ts).strftime("%Hh%M:%S") if ts else "?"
    send("⚡ <b>FOUDRE à %.0f km de %s</b>\nImpact à %s · position (%.3f, %.3f)\n"
         "<i>Réseau Blitzortung</i>" % (dist, point["nom"], heure, lat, lon))
    try:
        import radar
        png = radar.impact_map(lat, lon, point["lat"], point["lon"], point["nom"], dist)
        send_photo(png, "⚡ Impact de foudre à %.0f km de %s" % (dist, point["nom"]))
    except Exception as e:
        print("foudre map error:", e, flush=True)


def start_foudre():
    try:
        import foudre
    except Exception as e:
        print("foudre désactivé (paho-mqtt absent ?) :", e, flush=True)
        return
    pts = [{"nom": z.split(" (")[0], "lat": la, "lon": lo}
           for z, (la, lo) in m.ZONES.items()]
    foudre.FoudreWatcher(pts, on_impact, rayon_km=FOUDRE_RAYON_KM).start_background()
    print("foudre: watcher démarré (rayon %.0f km)" % FOUDRE_RAYON_KM, flush=True)


AIDE = (
    "🌤️ <b>MétéoGuy — ton expert météo</b>\n"
    "Zones : <b>Sablons (38550)</b>, Grury (71760), Lapeyrouse-Mornay (26210).\n\n"
    "<b>Commandes :</b>\n"
    "• /menu — menu à boutons\n"
    "• /matin — résumé du jour + reco habillage enfant 🎒\n"
    "• /meteo — météo du jour + créneaux de pluie + orages\n"
    "• /jour <code>quand</code> — <code>demain</code>, <code>lundi</code>, "
    "<code>weekend</code>, <code>+3</code>, <code>2026-06-13</code>\n"
    "• /orages <code>[quand]</code> — analyse orages des 3 zones (live)\n"
    "• /radar — image radar · /radaranim — radar animé 🎬\n"
    "• /sante — air + pollens + UV · /crue — vigilance crue\n"
    "• /vigilance — vigilance Météo-France (38/71/26)\n"
    "• /semaine — briefing 7 jours + El Niño\n"
    "• /elnino — point El Niño / ENSO\n"
    "• /alerte — contrôle orages/grêle immédiat\n\n"
    "<i>Auto : surveillance grêle (15-30 min), ⚡ alerte foudre &lt;50 km, "
    "message quotidien 6h50, briefing lundi.</i>"
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


def send_animation(gif, caption):
    token = m.load_token()
    boundary = "----MeteoGuyAnim7c3f"

    def field(name, value):
        return ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, name, value)).encode("utf-8")

    body = field("chat_id", OWNER) + field("caption", caption) + field("parse_mode", "HTML")
    body += ("--%s\r\nContent-Disposition: form-data; name=\"animation\"; "
             "filename=\"radar.gif\"\r\nContent-Type: image/gif\r\n\r\n" % boundary).encode()
    body += gif + b"\r\n" + ("--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendAnimation" % token, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r).get("ok", False)


# --------------------------------------------------------------------------- #
# Menus & commandes natives
# --------------------------------------------------------------------------- #
def main_menu_kb():
    return [
        [("🎒 Matin", "act:matin"), ("🌤️ Aujourd'hui", "act:meteo")],
        [("⛈️ Orages", "act:orages"), ("📡 Radar", "act:radar")],
        [("🎬 Radar animé", "act:radaranim"), ("🩺 Santé", "act:sante")],
        [("🚨 Vigilance", "act:vigilance"), ("🌊 Crue", "act:crue")],
        [("📅 Semaine", "act:semaine"), ("🌊 El Niño", "act:elnino")],
        [("🔔 Contrôle alerte", "act:alerte"), ("📆 Choisir un jour", "nav:jourmenu")],
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
        {"command": "matin", "description": "Résumé du jour + habillage enfant"},
        {"command": "meteo", "description": "Météo du jour à Sablons"},
        {"command": "jour", "description": "Météo d'un jour (demain, lundi, +3…)"},
        {"command": "orages", "description": "Analyse orages des 3 zones"},
        {"command": "radar", "description": "Image radar temps réel"},
        {"command": "radaranim", "description": "Radar animé (trajectoire)"},
        {"command": "sante", "description": "Air + pollens + UV"},
        {"command": "crue", "description": "Vigilance crue (Rhône)"},
        {"command": "vigilance", "description": "Vigilance Météo-France"},
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
    elif action in ("radar", "radaranim"):
        anim = action == "radaranim"
        send("📡 Génération du radar%s…" % (" animé" if anim else ""))
        try:
            import radar
            las = [la for la, lo in m.ZONES.values()]
            los = [lo for la, lo in m.ZONES.values()]
            center = (sum(las) / len(las), sum(los) / len(los))
            if anim:
                gif, ts = radar.radar_gif(center, zones=m.ZONES, zoom=8)
                send_animation(gif, "🎬 Radar animé — trajectoire des cellules (RainViewer)")
            else:
                png, ts = radar.radar_png(center, zones=m.ZONES, zoom=8)
                send_photo(png, radar.radar_caption(ts))
        except Exception as e:
            send("⚠️ Radar indisponible : %s" % e)
    elif action == "sante":
        txt, _ = m.render_sante()
        send(txt)
    elif action == "crue":
        send(m.render_crue())
    elif action == "matin":
        send(m.render_quotidien())
    elif action == "vigilance":
        lines = ["🚨 <b>Vigilance Météo-France</b>"]
        seen = False
        for z, dep in m.ZONE_DEPTS.items():
            v = m.vigilance(dep)
            if not v:
                continue
            seen = True
            items = ", ".join("%s %s" % (k, m.VIGI_COLORS[c])
                              for k, c in sorted(v.items(), key=lambda i: -i[1]) if c >= 2)
            lines.append("• <b>%s</b> (%d) : %s" % (z.split(" (")[0], dep, items or "🟢 RAS"))
        if not seen:
            lines.append("<i>Indisponible — clé METEOFRANCE_API_KEY non configurée.</i>")
        send("\n".join(lines))
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
    elif cmd in ("menu", "meteo", "radar", "radaranim", "elnino", "alerte",
                 "matin", "vigilance", "sante", "crue"):
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
    last_daily = datetime.date.today()   # évite l'envoi quotidien sur simple redémarrage
    while True:
        now = datetime.datetime.now()
        try:
            # message quotidien (pluie/temp/habillage enfant)
            if (now.hour, now.minute) >= DAILY_HHMM and last_daily != now.date():
                last_daily = now.date()
                send(m.render_quotidien())
                m.log_line("%s QUOTIDIEN envoyé" % now.strftime("%Y-%m-%d %H:%M"))
                # notifs intelligentes : alerter seulement si la prévision a changé
                try:
                    snap_path = os.path.join(m.DATA_DIR, "snapshot.json")
                    new = m.forecast_snapshot()
                    old = json.load(open(snap_path)) if os.path.exists(snap_path) else None
                    changes = m.diff_snapshot(old, new)
                    if changes:
                        send("🔔 <b>Changements de prévision</b>\n"
                             + "\n".join("• " + c for c in changes))
                    json.dump(new, open(snap_path, "w"))
                except Exception as e:
                    print("notif diff error:", e, flush=True)
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
    start_foudre()
    print("Bot démarré.", flush=True)
    poll_loop()


if __name__ == "__main__":
    main()
