# Imagem do assistente (API do site + agendador da coleta).
# O código entra por volume no docker-compose, então `git pull` já atualiza tudo sem reconstruir a imagem.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# tzdata: o agendador usa o fuso de São Paulo. curl: usado pelo healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001
CMD ["python", "api.py"]
