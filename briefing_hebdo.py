#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MétéoGuy — Briefing hebdomadaire (lundi).
Données multi-modèles + ENSO  ->  analyse rédigée par IA  ->  Telegram.

Cerveau d'analyse, par ordre de priorité :
  1. OpenAI (si OPENAI_API_KEY)         -> cible GitHub Actions / serveur
  2. CLI Claude local (si dispo)        -> cible poste Windows
  3. Repli déterministe (script)        -> si aucune IA disponible
"""
import subprocess, os, sys, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)
import meteoguy as m


def build_data():
    """Bloc de données déterministes : ensemble 7 modèles (zone principale +
    autres zones) + indice El Niño NOAA."""
    home = m.ZONES[m.HOME_ZONE]
    ens = m.compute_ensemble(*home)
    out = ["# DONNÉES MÉTÉOGUY — %s" % datetime.date.today().isoformat(),
           "## Zone principale : %s" % m.HOME_ZONE,
           m.fmt_ensemble_text(ens), "",
           "## Autres zones (aujourd'hui) :"]
    for z, (la, lo) in m.ZONES.items():
        if z == m.HOME_ZONE:
            continue
        try:
            e2 = m.compute_ensemble(la, lo, days=1)["days"][0]
            out.append("- %s : Tmax %.1f°C [%.0f–%.0f], pluie %d/%d modèles" % (
                z, e2["tmax"]["mean"], e2["tmax"]["min"], e2["tmax"]["max"],
                e2["rain_agreement"][0], e2["rain_agreement"][1]))
        except Exception:
            out.append("- %s : indisponible" % z)
    out.append("\n## El Niño / ENSO (NOAA CPC) :")
    try:
        e = m.enso_status()
        out.append("ONI %s = %+.2f°C — %s %s | tendance : %s" % (
            e["season"], e["oni"], e["phase"], e["direction"],
            " ".join("%+.2f" % v for v in e["trend"])))
    except Exception as ex:
        out.append("ENSO indisponible : %s" % ex)
    return "\n".join(out)


PROMPT = (
    "Tu es MétéoGuy, l'expert météo personnel de Stan pour Sablons (38550, Isère, "
    "vallée du Rhône). Données réelles du jour : ensemble de 7 modèles mondiaux "
    "(moyenne + accord/divergence) et indice El Niño/ENSO NOAA.\n\n%s\n\n"
    "Rédige le BRIEFING DU LUNDI pour Telegram. Règles :\n"
    "- Français, ton d'expert clair, tutoie Stan.\n"
    "- Format Telegram HTML : uniquement <b>gras</b> et <i>italique</i>, de vrais "
    "sauts de ligne, des emojis météo. AUCUNE autre balise, aucun bloc de code, aucun #.\n"
    "- Structure : 1) titre + date ; 2) tendance de la semaine à Sablons en exploitant "
    "l'ACCORD/DIVERGENCE des modèles (signale les divergences = fiabilité), coups de "
    "chaud/froid, pluie, vent ; 3) un mot sur Grury et Lapeyrouse-Mornay si notable ; "
    "4) section El Niño : état (ONI + tendance) et CONSÉQUENCES probables sur les "
    "prochains mois (monde + nuance honnête sur la France/vallée du Rhône, signal faible "
    "et indirect) ; si l'ONI approche/dépasse +0.5, souligne le basculement ; 5) une "
    "ligne de conseil. Concis, lisible sur téléphone. Réponds UNIQUEMENT avec le message."
)


def generate(data):
    prompt = PROMPT % data
    # 1) API LLM (Anthropic prioritaire, sinon OpenAI)
    msg = m.llm_analyze(prompt)
    if msg:
        brain = "Anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "OpenAI"
        return msg, brain
    # 2) Claude CLI local
    try:
        r = subprocess.run('claude -p --model claude-sonnet-4-6', input=prompt,
                           capture_output=True, cwd=HERE, encoding="utf-8",
                           errors="replace", shell=True, timeout=150)
        if (r.stdout or "").strip():
            return r.stdout.strip(), "Claude CLI"
    except Exception:
        pass
    return None, None


def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    m.log_line("%s BRIEFING %s" % (stamp, msg))


def main():
    try:
        data = build_data()
    except Exception as ex:
        log("échec données : %s" % ex); return
    msg, brain = generate(data)
    if msg:
        if msg.startswith("```"):
            msg = msg.strip("`").split("\n", 1)[-1]
        try:
            open(os.path.join(HERE, "briefing_draft.txt"), "w", encoding="utf-8").write(msg)
        except Exception:
            pass
        ok = m.send_telegram(msg)
        log("envoyé (%s)" % brain if ok else "ÉCHEC envoi (%s)" % brain)
        print("MÉTÉOGUY briefing -> Telegram %s · rédigé par %s" %
              ("OK" if ok else "ÉCHEC", brain))
    else:
        log("aucune IA -> repli déterministe")
        ok = m.send_telegram(m.render_briefing())
        print("MÉTÉOGUY briefing -> Telegram %s · repli déterministe (aucune IA)" %
              ("OK" if ok else "ÉCHEC"))


if __name__ == "__main__":
    main()
