FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt && useradd --create-home --uid 10001 ttvoice
COPY pyproject.toml ./
COPY thinktogether_voice ./thinktogether_voice
COPY tests ./tests
USER ttvoice
EXPOSE 8081
CMD ["python", "-m", "thinktogether_voice.worker", "start"]
