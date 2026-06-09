# 🌤️ MétéoGuy — Expert météo personnel

Surveillance météo + El Niño pour **Stan**, sur 3 zones, avec envoi **Telegram**.

| Zone | Code | Coordonnées | Rôle |
|------|------|-------------|------|
| **Sablons** | 38550 | 45.321 N, 4.774 E | Zone principale (briefing détaillé) |
| **Grury** | 71760 | 46.676 N, 3.911 E | Surveillée pour alertes |
| **Lapeyrouse-Mornay** | 26210 | 45.324 N, 4.995 E | Surveillée pour alertes |

## Deux services automatiques

### ⚡ Alerte orage / grêle — H-1 (méthode des ingrédients)
- Tâche Windows **`MeteoGuy_Alerte_Orage`**, toutes les **30 min**.
- Analyse les **2 prochaines heures** sur les 3 zones via **AROME 2,5 km (Météo-France)** pour
  les vents d'altitude + **GFS** pour lifted index / CIN / niveau de congélation.
- **Diagnostic convectif complet** (méthode SPC/ESTOFEX) par heure :
  - **CAPE** (instabilité), **cisaillement 0-6 km** et 0-1 km (calculés depuis les vents
    10 m / 850 / 500 hPa), **point de rosée** & rapport de mélange, **lifted index**, **CIN**,
    **lapse rate 700-500**, **T500**, **niveau de congélation**.
  - Indices composites : **SHIP** (Significant Hail Parameter) et **WMAXSHEAR** = √(2·CAPE)·shear.
- **4 niveaux** : VIGILANCE → ⛈️ ORAGE → 🧊⛈️ GRÊLE → 🧊🌪️ GROSSE GRÊLE/supercellule.
  Le **niveau de congélation (2400-3800 m) sert de gate** anti-fausse-alerte, et un **bonus
  flux de sud** (850 hPa 135-225°) signale la config grêligène classique du Rhône.
- Envoi Telegram **instantané**, sans IA (vitesse + fiabilité). Anti-spam via `alert_state.json`.
- Diagnostic à la demande : `python meteoguy.py diag 18` (12-18 h sur les 3 zones).

### 🗓️ Briefing hebdomadaire — lundi 07:30
- Tâche Windows **`MeteoGuy_Briefing_Lundi`**.
- `briefing_hebdo.py` : récupère l'**ensemble de 7 modèles mondiaux** (Météo-France, ECMWF,
  GFS, ICON, UKMO, GEM, JMA) + l'indice **El Niño (ONI, NOAA CPC)**, puis un **agent Claude
  (Sonnet)** rédige l'analyse (tendance, accord/divergence des modèles, conséquences El Niño)
  et l'envoie sur Telegram.

## Le moteur — `meteoguy.py`

```bash
python meteoguy.py test              # aperçu briefing + alertes, SANS envoi (console)
python meteoguy.py ensemble          # données multi-modèles + ENSO
python meteoguy.py alerte            # check orages/grêle H-1 -> Telegram si menace
python meteoguy.py orages [date]     # analyse orages complète, 3 zones (+ "send")
python meteoguy.py jour [date]       # analyse météo d'une journée (+ "send")
python meteoguy.py diag [heures]     # diagnostic convectif horaire détaillé
python meteoguy.py briefing          # briefing déterministe (repli sans IA) -> Telegram
python meteoguy.py send "txt"        # envoi d'un texte de test
```

## ☁️ Hébergement cloud — GitHub Actions (gratuit)

Le système tourne sur **GitHub Actions** (`.github/workflows/`), sans serveur :
- **`alerte.yml`** — toutes les 30 min ; anti-spam persisté via le cache Actions.
- **`briefing.yml`** — lundi 05h30 UTC (≈ 07h30 Paris) ; analyse rédigée par **Claude (API Anthropic)**.

**Secrets à définir** dans le repo (Settings → Secrets → Actions) :
| Secret | Rôle |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | token du bot |
| `TELEGRAM_CHAT_ID` | destinataire (`268329237`) |
| `ANTHROPIC_API_KEY` | analyse du briefing (Claude) |
| *(option)* `ANTHROPIC_MODEL` | défaut `claude-haiku-4-5-20251001` |

> L'analyse supporte aussi OpenAI en repli (`OPENAI_API_KEY` / `OPENAI_MODEL`) si la clé Anthropic est absente.

Le code lit ces valeurs via **variables d'environnement** (cloud) ou les **fichiers locaux**
(`~/.claude/.../.env` + `config.json`) en repli. Zéro dépendance pip (stdlib uniquement).

> ⚠️ Quand le cloud est actif, **désactiver les tâches Windows locales** pour éviter les
> doublons : `Disable-ScheduledTask -TaskName "MeteoGuy_*"`.

## Données & sources
- **Météo** : [Open-Meteo](https://open-meteo.com) (gratuit, sans clé) — multi-modèles + AROME HD.
- **El Niño / ENSO** : [NOAA CPC – ONI](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt) (indice officiel).
- **Telegram** : bot `@monclaudebot_bot`, token dans `~/.claude/channels/telegram/.env`,
  `chat_id` dans `config.json`.

## Fichiers
- `meteoguy.py` — moteur (météo, ensemble, ENSO, alertes, envoi Telegram).
- `briefing_hebdo.py` — orchestrateur du briefing du lundi (données → agent Claude → envoi).
- `config.json` — chat_id + zones surveillées.
- `logs/alerte.log` — journal des passes (RAS / alertes / briefings).
- `alert_state.json` — anti-doublon des alertes (créé au 1er orage détecté).
- `latest_ensemble.json` — dernier ensemble multi-modèles brut.

## Gérer les tâches planifiées
```powershell
Get-ScheduledTask -TaskName "MeteoGuy_*"                       # état
Start-ScheduledTask -TaskName "MeteoGuy_Alerte_Orage"          # forcer un check
Get-ScheduledTaskInfo -TaskName "MeteoGuy_Briefing_Lundi"      # prochaine exécution
Disable-ScheduledTask -TaskName "MeteoGuy_Alerte_Orage"        # mettre en pause
Unregister-ScheduledTask -TaskName "MeteoGuy_*"                # supprimer
```

> ⚠️ Les tâches s'exécutent quand la session Windows de Stan est ouverte (PC allumé).
> Pour des alertes 24/7 même PC éteint, il faudrait basculer en exécution cloud.
