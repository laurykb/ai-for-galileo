# Interface AI for Galileo

Front Next.js 16 de l’espace d’analyse Galileo. Il réunit le chat documentaire
multi-source, le suivi des preuves et l’atelier SRA pour les Risk Objectives,
scénarios et Target Objectives.

```bash
# depuis la racine
python serve.py

# ou uniquement le front
cd web
npm install
npm run dev
```

Avec `python serve.py`, l’interface utilise par défaut
<http://localhost:3001> et l’API <http://127.0.0.1:8001>. En mode front seul,
ces valeurs peuvent être surchargées avec `WEB_PORT` et
`NEXT_PUBLIC_API_BASE`.

```bash
npm run lint
npm run build
```
