FROM python:3.12-slim

# Fuseau Paris : indispensable pour la fenêtre H-1 et le briefing du lundi
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Paris

WORKDIR /app
# Pillow (cartes radar) + paho-mqtt (foudre Blitzortung)
RUN pip install --no-cache-dir pillow "paho-mqtt==1.6.1"
COPY meteoguy.py briefing_hebdo.py bot.py radar.py foudre.py ./

CMD ["python", "-u", "bot.py"]
