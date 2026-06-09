#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MétéoGuy — Expert météo personnel
=================================
Surveille 3 zones (Sablons 38550, Grury 71760, Lapeyrouse-Mornay 26210),
produit un briefing hebdomadaire (météo 7 jours + statut El Niño/ENSO NOAA)
et émet des alertes orages/grêle à H-1. Envoi direct sur Telegram.

Sources : Open-Meteo (multi-modèles + AROME HD France), NOAA CPC (ENSO).
Usage :
    python meteoguy.py briefing        # briefing hebdo (météo + El Niño) -> Telegram
    python meteoguy.py alerte          # check orages/grêle H-1 sur 3 zones -> Telegram si menace
    python meteoguy.py test            # affiche tout dans la console SANS envoyer
"""
import sys, os, json, time, urllib.request, urllib.parse, datetime

try:                       # console Windows -> UTF-8 (emojis)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "alert_state.json")
ENV_PATH = os.path.expanduser("~/.claude/channels/telegram/.env")

ZONES = {
    "Sablons (38550)":            (45.321,  4.7745),
    "Grury (71760)":              (46.676,  3.911),
    "Lapeyrouse-Mornay (26210)":  (45.324,  4.995),
}
HOME_ZONE = "Sablons (38550)"  # zone du briefing détaillé

ENSEMBLE_MODELS = [
    "meteofrance_seamless", "ecmwf_ifs025", "gfs_seamless",
    "icon_seamless", "ukmo_seamless", "gem_seamless", "jma_seamless",
]

WMO = {
    0: "☀️ Ciel clair", 1: "🌤️ Peu nuageux", 2: "⛅ Nuageux", 3: "☁️ Couvert",
    45: "🌫️ Brouillard", 48: "🌫️ Brouillard givrant",
    51: "🌦️ Bruine légère", 53: "🌦️ Bruine", 55: "🌧️ Bruine forte",
    61: "🌧️ Pluie faible", 63: "🌧️ Pluie", 65: "🌧️ Pluie forte",
    71: "🌨️ Neige faible", 73: "🌨️ Neige", 75: "❄️ Neige forte",
    80: "🌦️ Averses", 81: "🌦️ Averses", 82: "⛈️ Averses violentes",
    95: "⛈️ Orage", 96: "⛈️🧊 Orage + grêle", 99: "⛈️🧊 Orage + grêle forte",
}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
JOURS_LONG = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def fr_date(d):
    """Date longue en français (locale-indépendant) : 'samedi 13 juin 2026'."""
    return "%s %d %s %d" % (JOURS_LONG[d.weekday()], d.day, MOIS[d.month], d.year)


# --------------------------------------------------------------------------- #
# Réseau
# --------------------------------------------------------------------------- #
def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoGuy/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def log_line(text):
    try:
        logdir = os.path.join(HERE, "logs")
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "alerte.log"), "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def get_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoGuy/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def load_token():
    # priorité aux variables d'environnement (GitHub Actions / Docker), puis fichier local
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return os.environ["TELEGRAM_BOT_TOKEN"].strip()
    if not os.path.exists(ENV_PATH):
        return None
    for line in open(ENV_PATH, encoding="utf-8"):
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


def load_config():
    if os.path.exists(CONFIG_PATH):
        return json.load(open(CONFIG_PATH, encoding="utf-8"))
    return {}


def send_telegram(text):
    token = load_token()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or load_config().get("chat_id")
    if not token:
        print("⚠️  Token Telegram introuvable (", ENV_PATH, ")")
        return False
    if not chat_id:
        print("⚠️  chat_id manquant dans config.json — appairage Telegram non terminé.")
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token

    def _post(payload):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode())
            return get_json_post(req).get("ok", False)
        except Exception as e:
            print("⚠️  Telegram :", e)
            return False

    # 1) tentative HTML, 2) repli texte simple (tags retirés) si échec
    if _post({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true"}):
        return True
    import re
    plain = re.sub(r"<[^>]+>", "", text)
    return _post({"chat_id": chat_id, "text": plain,
                  "disable_web_page_preview": "true"})


def get_json_post(req):
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# --------------------------------------------------------------------------- #
# Analyse par IA (OpenAI) — sans dépendance, via API REST
# --------------------------------------------------------------------------- #
def llm_analyze(prompt, system=None, timeout=90):
    """Rédige une analyse via l'API OpenAI. Renvoie le texte, ou None si indispo.
    Modèle configurable via OPENAI_MODEL (défaut : gpt-5.4-mini)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=data,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
        return out["choices"][0]["message"]["content"].strip()
    except Exception as e:
        try:
            print("⚠️  OpenAI :", e.read().decode()[:300])
        except Exception:
            print("⚠️  OpenAI :", e)
        return None


# --------------------------------------------------------------------------- #
# Météo
# --------------------------------------------------------------------------- #
def forecast_7d(lat, lon):
    p = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,precipitation_probability_max,"
                 "wind_gusts_10m_max,uv_index_max",
        "timezone": "Europe/Paris", "forecast_days": 7,
    })
    return get_json("https://api.open-meteo.com/v1/forecast?" + p)["daily"]


def compute_ensemble(lat, lon, days=7):
    """Combine 7 modèles mondiaux et renvoie, par jour : moyenne, min, max
    (dispersion) pour Tmax/Tmin/pluie/rafales, + taux d'accord sur la pluie."""
    p = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,wind_gusts_10m_max",
        "models": ",".join(ENSEMBLE_MODELS),
        "timezone": "Europe/Paris", "forecast_days": days,
    })
    dd = get_json("https://api.open-meteo.com/v1/forecast?" + p)["daily"]
    times = dd["time"]

    def series(base):
        """{model: [valeurs par jour]} pour les modèles ayant la donnée."""
        out = {}
        for m in ENSEMBLE_MODELS:
            key = "%s_%s" % (base, m)
            if key in dd and any(v is not None for v in dd[key]):
                out[m] = dd[key]
        return out

    tmax, tmin = series("temperature_2m_max"), series("temperature_2m_min")
    psum, gust = series("precipitation_sum"), series("wind_gusts_10m_max")

    def agg(d, i):
        vals = [d[m][i] for m in d if d[m][i] is not None]
        if not vals:
            return None
        return {"mean": sum(vals) / len(vals), "min": min(vals),
                "max": max(vals), "n": len(vals)}

    out_days = []
    for i, t in enumerate(times):
        rains = [psum[m][i] for m in psum if psum[m][i] is not None]
        wet = sum(1 for r in rains if r >= 1.0)
        out_days.append({
            "date": t,
            "tmax": agg(tmax, i), "tmin": agg(tmin, i),
            "precip": agg(psum, i), "gust": agg(gust, i),
            "rain_agreement": (wet, len(rains)),  # modèles annonçant >=1mm / total
        })
    return {"n_models": len(tmax), "models": list(tmax.keys()), "days": out_days}


def fmt_ensemble_text(ens):
    """Rendu console/agent lisible de l'ensemble multi-modèles."""
    lines = ["ENSEMBLE %d modèles (%s)" % (ens["n_models"], ", ".join(ens["models"]))]
    for d in ens["days"]:
        dt = datetime.date.fromisoformat(d["date"])
        tx, tn = d["tmax"], d["tmin"]
        gp = d["gust"]
        wet, tot = d["rain_agreement"]
        spread = (tx["max"] - tx["min"]) if tx else 0
        conf = "accord" if spread <= 2 else ("divergence" if spread >= 4 else "modéré")
        lines.append(
            "%s %02d/%02d | Tmax %.1f°C [%.0f–%.0f, %s] | Tmin %.1f°C | "
            "pluie %d/%d modèles | rafales~%.0f km/h" % (
                JOURS[dt.weekday()], dt.day, dt.month,
                tx["mean"], tx["min"], tx["max"], conf,
                tn["mean"] if tn else 0, wet, tot,
                gp["mean"] if gp else 0))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Détection orage/grêle sophistiquée — « méthode des ingrédients »
# AROME France (CAPE, point de rosée, vents d'altitude, T500/T700, code) +
# GFS (lifted index, CIN, niveau de congélation). Indices SHIP & WMAXSHEAR.
# Réf : SPC, ESTOFEX, Taszarek et al. 2017.
# --------------------------------------------------------------------------- #
import math

AR = "meteofrance_arome_france"   # haute résolution France (vents d'altitude)
GF = "gfs_seamless"               # seul à fournir LI / CIN / freezing level

SEVERE_HOURLY = (
    "cape,dew_point_2m,temperature_2m,surface_pressure,wind_speed_10m,"
    "wind_direction_10m,wind_gusts_10m,weather_code,precipitation,"
    "wind_speed_850hPa,wind_direction_850hPa,wind_speed_500hPa,"
    "wind_direction_500hPa,geopotential_height_700hPa,geopotential_height_500hPa,"
    "temperature_700hPa,temperature_500hPa,lifted_index,convective_inhibition,"
    "freezing_level_height"
)


def _uv(speed, direction):
    """Composantes (u, v) d'un vent météo (direction = d'où il vient), en m/s."""
    if speed is None or direction is None:
        return None
    r = math.radians(direction)
    return (-speed * math.sin(r), -speed * math.cos(r))


def _shear(uv_lo, uv_hi):
    if not uv_lo or not uv_hi:
        return None
    return math.hypot(uv_hi[0] - uv_lo[0], uv_hi[1] - uv_lo[1])


def _mixing_ratio(td, psfc):
    """Rapport de mélange (g/kg) depuis point de rosée (°C) et pression (hPa)."""
    if td is None or psfc is None:
        return None
    e = 6.112 * math.exp(17.67 * td / (td + 243.5))
    return 622.0 * e / (psfc - e)


def _ship(cape, mixr, lr75, t500, shear06, fl):
    """SHIP simplifié (Significant Hail Parameter, SPC)."""
    if None in (cape, mixr, lr75, t500, shear06) or cape <= 0:
        return None
    mixr = min(max(mixr, 11.0), 13.6)
    sh = min(max(shear06, 7.0), 27.0)
    t5 = min(t500, -5.5)
    ship = (cape * mixr * lr75 * (-t5) * sh) / 42_000_000.0
    if cape < 1300:
        ship *= cape / 1300.0
    if lr75 < 5.8:
        ship *= lr75 / 5.8
    if fl is not None and fl < 2400:
        ship *= fl / 2400.0
    return ship


# modèles dont on agrège le "code orage" pour ne rien rater
STORM_CODE_MODELS = [AR, "meteofrance_arpege_europe", "icon_d2", GF]


def _multimodel_storm_codes(lat, lon, days=2):
    """{timestamp ISO -> plus haut code orage (95-99) annoncé par un modèle}.
    Combine AROME + ARPEGE + ICON-D2 + GFS pour ne manquer aucun orage."""
    p = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": "weather_code",
        "models": ",".join(STORM_CODE_MODELS),
        "timezone": "Europe/Paris", "forecast_days": days,
    })
    try:
        h = get_json("https://api.open-meteo.com/v1/forecast?" + p)["hourly"]
    except Exception:
        return {}
    times = h["time"]
    out = {}
    for i, t in enumerate(times):
        codes = []
        for mdl in STORM_CODE_MODELS:
            v = h.get("weather_code_%s" % mdl)
            if v and i < len(v) and v[i] is not None and v[i] >= 95:
                codes.append(v[i])
        out[t] = max(codes) if codes else None
    return out


def analyse_severe(lat, lon, hours=2, day=None):
    """Diagnostic convectif complet (ingrédients + indices + niveau).
    Par défaut : les `hours` prochaines heures. Si `day` (date) est fourni :
    toutes les heures de cette journée."""
    fdays = 2 if day is None else max(2, (day - datetime.date.today()).days + 1)
    fdays = min(fdays, 16)
    p = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": SEVERE_HOURLY,
        "models": "%s,%s" % (AR, GF), "wind_speed_unit": "ms",
        "timezone": "Europe/Paris", "forecast_days": fdays,
    })
    h = get_json("https://api.open-meteo.com/v1/forecast?" + p)["hourly"]
    codes_map = _multimodel_storm_codes(lat, lon, days=fdays)

    def g(base, model, i):
        v = h.get("%s_%s" % (base, model))
        return v[i] if v and i < len(v) and v[i] is not None else None

    now = datetime.datetime.now()
    out = []
    for i, t in enumerate(h["time"]):
        dt = datetime.datetime.fromisoformat(t)
        if day is not None:
            if dt.date() != day:
                continue
        else:
            delta = (dt - now).total_seconds() / 3600.0
            if not (0 <= delta <= hours + 0.5):
                continue

        cape = g("cape", AR, i)
        td = g("dew_point_2m", AR, i)
        psfc = g("surface_pressure", AR, i)
        gust = g("wind_gusts_10m", AR, i)
        code = g("weather_code", AR, i)
        precip = g("precipitation", AR, i)
        t500 = g("temperature_500hPa", AR, i)
        t700 = g("temperature_700hPa", AR, i)
        gh500 = g("geopotential_height_500hPa", AR, i)
        gh700 = g("geopotential_height_700hPa", AR, i)
        uv10 = _uv(g("wind_speed_10m", AR, i), g("wind_direction_10m", AR, i))
        uv850 = _uv(g("wind_speed_850hPa", AR, i), g("wind_direction_850hPa", AR, i))
        uv500 = _uv(g("wind_speed_500hPa", AR, i), g("wind_direction_500hPa", AR, i))
        wd850 = g("wind_direction_850hPa", AR, i)
        # GFS pour les paramètres absents d'AROME
        li = g("lifted_index", GF, i)
        cin = g("convective_inhibition", GF, i)
        fl = g("freezing_level_height", GF, i)
        if cape is None:
            cape = g("cape", GF, i)

        shear06 = _shear(uv10, uv500)
        shear01 = _shear(uv10, uv850)
        mixr = _mixing_ratio(td, psfc)
        lr75 = None
        if None not in (t700, t500, gh700, gh500) and gh500 > gh700:
            lr75 = (t700 - t500) / ((gh500 - gh700) / 1000.0)
        wmax = math.sqrt(2 * cape) if cape and cape > 0 else None
        wmaxshear = wmax * shear06 if (wmax and shear06) else None
        ship = _ship(cape, mixr, lr75, t500, shear06, fl)

        out.append({
            "dt": dt, "code": code, "code_multi": codes_map.get(t),
            "gust": gust, "precip": precip,
            "cape": cape, "td": td, "t500": t500, "li": li, "cin": cin,
            "fl": fl, "shear06": shear06, "shear01": shear01, "mixr": mixr,
            "lr75": lr75, "wmaxshear": wmaxshear, "ship": ship, "wd850": wd850,
        })
    return out


def classify_hour(x):
    """Niveau d'alerte 0-4 et libellé — UNION de toutes les sources pour ne rien rater :
    code orage multi-modèles (AROME+ARPEGE+ICON+GFS) OU environnement convectif explosif.
    0=rien, 1=vigilance, 2=orage, 3=grêle, 4=grosse grêle/supercellule."""
    # code orage = le plus sévère annoncé par n'importe quel modèle
    code = x["code"]
    cm = x.get("code_multi")
    code_eff = max([c for c in (code, cm) if c is not None], default=None)

    sh = x["shear06"]
    fl = x["fl"]
    cape = x["cape"] or 0
    t500 = x["t500"]
    ship = x["ship"]
    wms = x["wmaxshear"]
    li = x["li"]

    storm_now = (code_eff is not None and code_eff >= 95) or \
                (x["precip"] and x["precip"] >= 6 and cape >= 600)
    # environnement sévère « primé » même sans orage encore affiché par les modèles
    severe_env = (cape >= 800 and sh is not None and sh >= 15
                  and (li is None or li <= -4)
                  and ((ship is not None and ship >= 0.5)
                       or (wms is not None and wms >= 400)))

    if not storm_now and not severe_env:
        return 0, ""
    if not storm_now and severe_env:
        return 1, "VIGILANCE (potentiel orageux)"

    freezing_ok = fl is not None and 2400 <= fl <= 3800
    hail_idx = (ship is not None and ship >= 1.0) or (wms is not None and wms >= 700)
    big_idx = (ship is not None and ship >= 1.5) or (wms is not None and wms >= 900)
    code = code_eff  # pour les tests 96/99 ci-dessous

    level, label = 2, "ORAGE"
    # grêle : orage organisé + indices + gate niveau de congélation + air froid à 500 hPa
    if (code in (96, 99)) or (
        cape >= 1200 and sh is not None and sh >= 15 and freezing_ok
        and (t500 is None or t500 <= -15) and hail_idx):
        level, label = 3, "GRÊLE"
    # grosse grêle / supercellule
    if (cape >= 2000 and sh is not None and sh >= 20 and freezing_ok
            and (t500 is not None and t500 <= -18) and big_idx):
        level, label = 4, "GROSSE GRÊLE / supercellule"
    return level, label


# --------------------------------------------------------------------------- #
# ENSO / El Niño (NOAA CPC)
# --------------------------------------------------------------------------- #
def enso_status():
    """Lit l'ONI (Oceanic Niño Index) officiel NOAA et renvoie un résumé."""
    txt = get_text("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt")
    rows = []
    for line in txt.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 4:
            seas, yr, total, anom = parts
            try:
                rows.append((seas, int(yr), float(anom)))
            except ValueError:
                pass
    if not rows:
        return None
    last = rows[-1]
    trend = [r[2] for r in rows[-5:]]  # 5 dernières saisons glissantes
    oni = last[2]
    if oni >= 2.0:      phase = "El Niño TRÈS FORT (super)"
    elif oni >= 1.5:    phase = "El Niño fort"
    elif oni >= 1.0:    phase = "El Niño modéré"
    elif oni >= 0.5:    phase = "El Niño faible"
    elif oni > -0.5:    phase = "Neutre (ENSO-neutre)"
    elif oni > -1.0:    phase = "La Niña faible"
    else:               phase = "La Niña"
    direction = "↗️ en hausse" if trend[-1] > trend[0] else ("↘️ en baisse" if trend[-1] < trend[0] else "→ stable")
    return {
        "season": "%s %d" % (last[0], last[1]),
        "oni": oni, "phase": phase, "trend": trend, "direction": direction,
    }


# --------------------------------------------------------------------------- #
# Rendu des messages
# --------------------------------------------------------------------------- #
def render_briefing():
    today = datetime.date.today()
    d = forecast_7d(*ZONES[HOME_ZONE])
    lines = ["🌤️ <b>MétéoGuy — Briefing hebdo</b>",
             "📍 <b>%s</b> · %s" % (HOME_ZONE, today.strftime("%d/%m/%Y")), ""]
    lines.append("<b>Prévisions 7 jours :</b>")
    for i, day in enumerate(d["time"]):
        dt = datetime.date.fromisoformat(day)
        code = WMO.get(d["weather_code"][i], "?")
        tmin, tmax = d["temperature_2m_min"][i], d["temperature_2m_max"][i]
        pp = d["precipitation_probability_max"][i]
        psum = d["precipitation_sum"][i]
        gust = d["wind_gusts_10m_max"][i]
        rain = (" · 🌧️%.0f%%/%.0fmm" % (pp, psum)) if pp and pp >= 30 else ""
        windw = (" · 💨%.0f" % gust) if gust and gust >= 50 else ""
        lines.append("%s %02d : %s  %.0f→%.0f°C%s%s" %
                     (JOURS[dt.weekday()], dt.day, code, tmin, tmax, rain, windw))

    # mini-résumé des 2 autres zones (max du jour)
    lines.append("")
    lines.append("<b>Autres zones (aujourd'hui) :</b>")
    for z, (la, lo) in ZONES.items():
        if z == HOME_ZONE:
            continue
        try:
            dz = forecast_7d(la, lo)
            lines.append("• %s : %s  %.0f→%.0f°C" % (
                z, WMO.get(dz["weather_code"][0], "?"),
                dz["temperature_2m_min"][0], dz["temperature_2m_max"][0]))
        except Exception:
            lines.append("• %s : (données indisponibles)" % z)

    # ENSO / El Niño
    lines.append("")
    lines.append("🌊 <b>Statut El Niño (NOAA CPC) :</b>")
    try:
        e = enso_status()
        if e:
            tr = " → ".join("%+.2f" % v for v in e["trend"])
            lines.append("ONI %s : <b>%+.2f°C</b> — %s %s" %
                         (e["season"], e["oni"], e["phase"], e["direction"]))
            lines.append("Tendance (5 saisons) : %s" % tr)
            if e["oni"] >= 0.5:
                lines.append("⚠️ Conditions El Niño officiellement en place.")
            elif e["trend"][-1] - e["trend"][0] >= 0.3:
                lines.append("📈 Réchauffement du Pacifique : El Niño en formation.")
        else:
            lines.append("(données ENSO indisponibles)")
    except Exception as ex:
        lines.append("(ENSO indisponible : %s)" % ex)

    lines.append("")
    lines.append("<i>Sources : Open-Meteo (Météo-France/ECMWF/GFS), NOAA CPC.</i>")
    return "\n".join(lines)


LEVEL_ICON = {1: "👀", 2: "⛈️", 3: "🧊⛈️", 4: "🧊🌪️"}


def _ingredients_line(x):
    """Ligne lisible des ingrédients convectifs déterminants."""
    p = []
    if x["cape"] is not None:     p.append("CAPE %.0f" % x["cape"])
    if x["shear06"] is not None:  p.append("cisaill. 0-6km %.0f m/s" % x["shear06"])
    if x["ship"] is not None:     p.append("SHIP %.1f" % x["ship"])
    if x["wmaxshear"] is not None: p.append("WMAXSHEAR %.0f" % x["wmaxshear"])
    if x["fl"] is not None:       p.append("isotherme 0°C %.0f m" % x["fl"])
    if x["t500"] is not None:     p.append("T500 %.0f°C" % x["t500"])
    if x["li"] is not None:       p.append("LI %.0f" % x["li"])
    if x["td"] is not None:       p.append("Td %.0f°C" % x["td"])
    if x["gust"] is not None:     p.append("rafales %.0f km/h" % (x["gust"] * 3.6))
    return " · ".join(p)


LEVEL_NAME = {0: "calme", 1: "vigilance", 2: "orage", 3: "grêle", 4: "grosse grêle"}


def storm_day_summary(lat, lon, day):
    """Synthèse orageuse d'une journée : niveau max, heures à risque, pic d'ingrédients."""
    rows = analyse_severe(lat, lon, day=day)
    risky = []
    peak_lvl, peak = 0, None
    for x in rows:
        lvl, lbl = classify_hour(x)
        if lvl >= 1:
            risky.append((x["dt"], lvl, lbl))
            if lvl > peak_lvl:
                peak_lvl, peak = lvl, x
    cape_max = max([x["cape"] for x in rows if x["cape"] is not None], default=None)
    sh_max = max([x["shear06"] for x in rows if x["shear06"] is not None], default=None)
    return {"rows": rows, "risky": risky, "peak_lvl": peak_lvl, "peak": peak,
            "cape_max": cape_max, "shear_max": sh_max}


def render_orages_report(day):
    """Analyse orages COMPLÈTE des 3 zones pour une journée (texte HTML Telegram)."""
    lines = ["⛈️ <b>Analyse orages — %s</b>" % fr_date(day).capitalize(), ""]
    any_risk = False
    for zone, (la, lo) in ZONES.items():
        try:
            s = storm_day_summary(la, lo, day)
        except Exception as ex:
            lines.append("<b>%s</b> : données indisponibles (%s)" % (zone, ex)); continue
        lvl = s["peak_lvl"]
        icon = LEVEL_ICON.get(lvl, "✅")
        if lvl == 0:
            lines.append("%s <b>%s</b> : pas d'orage attendu" % ("✅", zone))
            cm = ("(CAPE max %.0f, cisaill. max %.0f m/s)" %
                  (s["cape_max"] or 0, s["shear_max"] or 0))
            lines.append("   <i>%s</i>" % cm)
            continue
        any_risk = True
        heures = ", ".join("%dh" % d.hour for d, _, _ in s["risky"])
        lines.append("%s <b>%s</b> — risque max : <b>%s</b>" %
                     (icon, zone, LEVEL_NAME[lvl].upper()))
        lines.append("   Créneaux : %s" % heures)
        if s["peak"]:
            lines.append("   Pic : <i>%s</i>" % _ingredients_line(s["peak"]))
    lines.append("")
    lines.append("<i>Union AROME+ARPEGE+ICON+GFS · SHIP/WMAXSHEAR · "
                 "(AROME haute-réso ≤ +2 j ; au-delà, fiabilité moindre).</i>")
    if not any_risk:
        lines.insert(2, "✅ <b>Aucun orage significatif attendu sur les 3 zones.</b>\n")
    return "\n".join(lines)


def render_alertes():
    """Renvoie (message ou None, liste des nouveaux évènements à mémoriser)."""
    state = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {"sent": []}
    already = set(state.get("sent", []))
    blocks, new_keys = [], []

    for zone, (la, lo) in ZONES.items():
        try:
            hrs = analyse_severe(la, lo, hours=2)
        except Exception:
            continue
        scored = []
        for x in hrs:
            lvl, lbl = classify_hour(x)
            if lvl >= 1:
                scored.append((lvl, x, lbl))
        if not scored:
            continue
        lvl, x, lbl = max(scored, key=lambda s: (s[0], s[1]["dt"]))  # pire menace
        key = "%s|%s|%d" % (zone, x["dt"].strftime("%Y-%m-%dT%H"), lvl)
        if key in already:
            continue
        new_keys.append(key)

        eta = x["dt"].strftime("%Hh%M")
        # bonus contexte : flux de sud grêligène en vallée du Rhône
        flux = ""
        if x["wd850"] is not None and 135 <= x["wd850"] <= 225 and lvl >= 3:
            flux = "\n   ↳ <i>flux de sud : configuration grêligène classique du Rhône</i>"
        blocks.append("%s <b>%s</b> — arrivée vers <b>%s</b>\n   <b>%s</b>\n   <i>%s</i>%s" % (
            LEVEL_ICON.get(lvl, "⛈️"), zone, eta, lbl, _ingredients_line(x), flux))

    if not blocks:
        return None, new_keys
    if any("GRÊLE" in b for b in blocks):
        top, head = "🚨 ALERTE GRÊLE — H-1", "🛡️ Protégez véhicules, cultures, volets, animaux."
    elif any("ORAGE" in b for b in blocks):
        top, head = "🚨 ALERTE ORAGE — H-1", "🛡️ Prudence : rafales, foudre, fortes pluies possibles."
    else:
        top, head = "👀 VIGILANCE ORAGES", "🔎 Atmosphère instable : orages possibles dans les prochaines heures."
    msg = ("<b>%s</b>\n<i>%s</i>\n\n%s\n\n%s\n"
           "<i>Union AROME+ARPEGE+ICON+GFS · méthode des ingrédients (SHIP/WMAXSHEAR).</i>" %
           (top, datetime.datetime.now().strftime("%d/%m %H:%M"), "\n\n".join(blocks), head))
    return msg, new_keys


def save_state(new_keys):
    state = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {"sent": []}
    sent = state.get("sent", [])
    sent.extend(new_keys)
    # purge : ne garder que les créneaux récents (par tri lexical sur la date ISO)
    cutoff = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    sent = [k for k in sent if k.split("|", 1)[1][:10] >= cutoff]
    json.dump({"sent": sorted(set(sent))}, open(STATE_PATH, "w"), indent=2)


# --------------------------------------------------------------------------- #
# Entrée
# --------------------------------------------------------------------------- #
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    if mode == "briefing":
        msg = render_briefing()
        ok = send_telegram(msg)
        print("Briefing envoyé." if ok else "Briefing NON envoyé.")
        print(msg)

    elif mode == "alerte":
        msg, new_keys = render_alertes()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if msg:
            ok = send_telegram(msg)
            if ok:
                save_state(new_keys)
            log_line("%s ALERTE %s : %s" % (stamp, "envoyée" if ok else "ÉCHEC",
                     ", ".join(new_keys)))
            print(("Alerte envoyée." if ok else "Alerte NON envoyée."), "\n", msg)
        else:
            log_line("%s RAS" % stamp)
            print("[%s] RAS — aucun orage/grêle dans les 2 h sur les 3 zones." % stamp)

    elif mode == "send":          # envoi d'un texte direct (tests)
        ok = send_telegram(sys.argv[2] if len(sys.argv) > 2 else "(vide)")
        print("Envoyé." if ok else "Échec envoi.")

    elif mode == "sendfile":      # envoi du contenu d'un fichier (briefing rédigé par l'agent)
        path = sys.argv[2]
        text = open(path, encoding="utf-8-sig").read().strip()
        ok = send_telegram(text)
        print("Briefing envoyé." if ok else "Briefing NON envoyé.")

    elif mode == "ensemble":
        # Données brutes multi-modèles + ENSO pour l'agent Claude hebdo.
        ens = compute_ensemble(*ZONES[HOME_ZONE])
        block = ["# DONNÉES MÉTÉOGUY — %s" % datetime.date.today().isoformat(),
                 "## Zone principale : %s" % HOME_ZONE,
                 fmt_ensemble_text(ens), "",
                 "## Autres zones surveillées (aujourd'hui, ensemble) :"]
        for z, (la, lo) in ZONES.items():
            if z == HOME_ZONE:
                continue
            try:
                e2 = compute_ensemble(la, lo, days=1)["days"][0]
                block.append("- %s : Tmax %.1f°C [%.0f–%.0f], pluie %d/%d modèles" % (
                    z, e2["tmax"]["mean"], e2["tmax"]["min"], e2["tmax"]["max"],
                    e2["rain_agreement"][0], e2["rain_agreement"][1]))
            except Exception:
                block.append("- %s : indisponible" % z)
        block.append("\n## El Niño / ENSO (NOAA CPC) :")
        try:
            e = enso_status()
            block.append("ONI %s = %+.2f°C — %s %s | tendance 5 saisons : %s" % (
                e["season"], e["oni"], e["phase"], e["direction"],
                " ".join("%+.2f" % v for v in e["trend"])))
        except Exception as ex:
            block.append("ENSO indisponible : %s" % ex)
        text = "\n".join(block)
        json.dump({"generated": datetime.date.today().isoformat(),
                   "ensemble": ens}, open(os.path.join(HERE, "latest_ensemble.json"), "w"))
        print(text)

    elif mode == "orages":        # analyse orages complète d'une journée (def. aujourd'hui)
        args = sys.argv[2:]
        send = "send" in args
        dates = [a for a in args if a != "send"]
        day = datetime.date.fromisoformat(dates[0]) if dates else datetime.date.today()
        rpt = render_orages_report(day)
        if send:
            print("Envoyé." if send_telegram(rpt) else "Échec envoi.")
        print(rpt)

    elif mode == "jour":          # analyse météo d'une journée (ensemble + orages)
        args = sys.argv[2:]
        send = "send" in args
        dates = [a for a in args if a != "send"]
        day = datetime.date.fromisoformat(dates[0]) if dates else datetime.date.today()
        di = (day - datetime.date.today()).days
        ens = compute_ensemble(*ZONES[HOME_ZONE], days=min(max(di + 1, 1), 16))
        match = next((d for d in ens["days"] if d["date"] == day.isoformat()), None)
        lines = ["📅 <b>Analyse météo — %s</b>" % fr_date(day).capitalize(),
                 "📍 %s" % HOME_ZONE, ""]
        if match:
            tx, tn = match["tmax"], match["tmin"]
            wet, tot = match["rain_agreement"]
            spread = (tx["max"] - tx["min"]) if tx else 0
            conf = "modèles d'accord" if spread <= 2 else ("modèles divergents (±%.0f°C)" % spread if spread >= 4 else "accord modéré")
            lines.append("🌡️ Tmax <b>%.0f°C</b> (%.0f–%.0f, %s) · Tmin %.0f°C" % (
                tx["mean"], tx["min"], tx["max"], conf, tn["mean"] if tn else 0))
            lines.append("🌧️ Pluie : %d/%d modèles · 💨 rafales ~%.0f km/h" % (
                wet, tot, match["gust"]["mean"] if match["gust"] else 0))
        else:
            lines.append("(hors de portée des modèles d'ensemble)")
        lines.append("")
        lines.append(render_orages_report(day))
        txt = "\n".join(lines)
        if send:
            print("Envoyé." if send_telegram(txt) else "Échec envoi.")
        print(txt)

    elif mode == "diag":          # diagnostic convectif détaillé (12 h) sur les 3 zones
        hrs_ahead = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        for zone, (la, lo) in ZONES.items():
            print("\n=== %s — prochaines %dh ===" % (zone, hrs_ahead))
            try:
                rows = analyse_severe(la, lo, hours=hrs_ahead)
            except Exception as ex:
                print("  erreur:", ex); continue
            shown = 0
            for x in rows:
                lvl, lbl = classify_hour(x)
                interesting = (x["code"] is not None and x["code"] >= 80) or \
                              (x["cape"] or 0) >= 300 or lvl >= 2
                if not interesting:
                    continue
                shown += 1
                flag = (" -> %s" % lbl) if lvl >= 2 else ""
                print("  %s | code %s | %s%s" % (
                    x["dt"].strftime("%a %Hh"), x["code"], _ingredients_line(x), flag))
            if not shown:
                print("  (rien de convectif : CAPE faible, pas d'averses)")

    elif mode == "test":
        print("=" * 60, "\nBRIEFING (aperçu, non envoyé)\n", "=" * 60, sep="")
        print(render_briefing())
        print("\n" + "=" * 60, "\nALERTES (aperçu, non envoyé)\n", "=" * 60, sep="")
        msg, _ = render_alertes()
        print(msg if msg else "RAS — aucun orage/grêle dans les 2 h.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
