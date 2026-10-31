FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest==8.4.1 pytest-cov==6.2.1

WORKDIR /app
COPY tinydb/ /app/

# Make the package importable without a build step, and give the agent a
# clean git baseline so `git diff` shows exactly what it changed.
ENV PYTHONPATH=/app
RUN git init -q \
    && git config user.email "task@example.com" \
    && git config user.name "task" \
    && git add -A \
    && git commit -qm "TinyDB 4.8.2"
