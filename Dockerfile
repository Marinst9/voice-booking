# The image that runs the API (and the migrations, see docker-compose.yml).
FROM python:3.14-slim

# Print logs immediately; don't write .pyc files; keep pip quiet and the image small.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first: Docker caches this layer, so code changes rebuild fast.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# Don't run as root inside the container.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000
# PORT: hosting platforms like Railway tell the app which port to listen on; locally it's 8000.
# `exec` makes uvicorn the main process, so it receives the platform's stop signal and shuts down cleanly.
# --no-access-log: our own middleware already logs every request (as JSON).
# Migrations are NOT run here: Docker Compose runs them in the `migrate` service, Railway in its
# pre-deploy command (see README "Deploying to Railway").
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
