# Debian-bas: weasyprint fungerar out-of-the-box med pango/cairo-libs
# från apt, medan nixpacks-grenen krashade på "cannot load library
# 'libgobject-2.0-0'" (LD_LIBRARY_PATH pekade inte på nix-store-libsen
# vid runtime även om paketen var installerade).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# weasyprint system-deps enligt officiell Debian-guide:
# https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#debian-ubuntu
# libpango-1.0-0 drar in libglib2.0-0 + libgobject-2.0-0 som
# transitive deps, men vi listar dem explicit för att göra det
# tydligt för framtida uppgraderingar.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libglib2.0-0 \
    libcairo2 \
    libffi8 \
    fonts-liberation \
    fonts-dejavu-core \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 för att bygga frontend-bundeln (Vite)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python-deps först (cache-vänligt)
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Frontend: install → build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# Backend-kod
COPY app/ ./app/

# Railway sätter PORT vid deploy. Default 8080 lokalt.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
