# web/ — AI for SSH (interface cible)

Front **Next.js (App Router) + Tailwind, TypeScript** : l'interface de
l'application (`python serve.py`). Direction artistique : dark OLED, motion
sobre, kit UI maison (`src/components/ui.tsx`) — avec l'accent périwinkle
propre à AI for SSH. Pages : Chat RAG (SSE, sources, boîte de verre), Documents
& ingestion, Observabilité (mode expert), Paramètres — thème Thales (nuit
bleue + aqua), intro animée.

## Lancer en local

Prérequis : Node ≥ 20 (installé via nvm : `nvm use default`).

```bash
python serve.py   # depuis la racine : Mongo + Ollama + API :8000 + front :3000
```

ou, front seul (sans l'API) :

```bash
bash dev.sh             # depuis web/ — équivaut à npm run dev
```

Ouvre <http://localhost:3000>. L'API est attendue sur `http://127.0.0.1:8000`
(surchargable via `NEXT_PUBLIC_API_BASE` dans `.env.local`). Première fois :
`npm install` (réseau requis une fois ; les fonts Google sont self-hostées au
build par `next/font`, rien ne sort de la machine au runtime).

## Vérifications

```bash
npm run lint
npm run build
```
