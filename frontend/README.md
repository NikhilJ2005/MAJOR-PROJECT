# VibeStack frontend

Next.js (App Router, static export) + Tailwind. In production the FastAPI backend
serves `out/` from the same origin, so there is no CORS and only one Railway service.

```bash
npm install
npm run dev          # http://localhost:3000, talks to NEXT_PUBLIC_API_URL (see .env.development)
npm run build        # writes out/, which the backend serves automatically if present
```

Run the backend with `FAKE_LLM=1` to work on the UI without an API key.
