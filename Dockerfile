FROM python:3.12-slim

# Fuseau Paris : indispensable pour la fenêtre H-1 et le briefing du lundi
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Paris

WORKDIR /app
COPY meteoguy.py briefing_hebdo.py bot.py ./

# Aucune dépendance pip (stdlib uniquement)
CMD ["python", "-u", "bot.py"]
