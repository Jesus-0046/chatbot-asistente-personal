FROM python:3.12-slim AS base

# Herramientas ligeras para healthchecks en el contenedor (pgrep para
# verificar el proceso del bot/API; la imagen slim no las incluye).
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

# La app no debe ejecutarse como root en el contenedor.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin asistente \
    && chown -R asistente:asistente /app
USER asistente

# El entrypoint por defecto ejecuta las migraciones y arranca el bot.
# docker-compose sobrescribe CMD para el servicio `api`.
CMD ["sh", "-c", "alembic upgrade head && python -m asistente.main"]