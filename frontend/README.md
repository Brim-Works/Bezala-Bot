# Bezala Bot — Frontend

React + Vite-dashboard för Bezala Bot.

## Dev

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxar /api till :8000
```

## Build

```bash
npm run build        # skapar frontend/dist/
```

FastAPI serverar `frontend/dist/` automatiskt på `/` om mappen finns. Railway-
deploy kör både `npm run build` och `uvicorn` via nixpacks — se `nixpacks.toml`.
