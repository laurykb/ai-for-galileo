# AI for Galileo hors ligne — guide unique

Le paquet contient l'application complète : frontend, API, MongoDB, Ollama,
données documentaires, index et tous les modèles disponibles sur la source.

## Prérequis

Matériel cible validé : AMD Ryzen Threadripper PRO 7945WX, 503 Gio de RAM et deux NVIDIA RTX 6000 Ada de 48 Gio. Le runtime Docker `nvidia` est obligatoire et contrôlé automatiquement. Ollama répartit les modèles sur les deux GPU.

- source : Linux x86-64, Docker avec le plugin Compose, Ollama, l'environnement
  Python du projet et MongoDB accessible sur localhost:27017 ;
- cible isolée : Ubuntu 24.04 x86-64 avec Docker/Compose, Ollama, Python 3 et VS Code ;
- support de transfert d'au moins 150 Gio ;
- disque local cible avec au moins 200 Gio libres ;
- ext4 recommandé pour le support et obligatoire pour le dossier d'exécution local.

Python 3 est utilisé uniquement pour le petit lanceur `serve.py` de la cible.
Node.js, npm, MongoDB, Ollama et les dépendances Python sont dans les images.

## Préparer sur la source

```bash
bash deploy/offline.sh prepare /chemin/vers/ai-for-ssh-v3
```

Cette commande valide l'application, construit les images, sauvegarde `ragdb`,
copie les données et le magasin complet des modèles de `ollama list`, puis
produit les sommes SHA-256. Pour un emplacement Ollama atypique, définir
`OLLAMA_MODELS_DIR=/chemin/vers/models`.

## Installer et lancer sur la cible

Copier d'abord le paquet du support vers le disque local, notamment avec Docker Snap :

```bash
cp -a /media/$USER/AI_FOR_SSH/ai-for-ssh-v3 "$HOME/ai-for-ssh-v3"
cd "$HOME/ai-for-ssh-v3"
bash deploy/offline.sh verify
python3 serve.py
bash deploy/offline.sh smoke
```

Le premier lancement charge les images, recrée le volume MongoDB, restaure le dump et monte les modèles Ollama embarqués.
Les suivants réutilisent les volumes. Sur la cible, ouvrir
`http://localhost:3001`. La commande `smoke` vérifie le frontend, FastAPI,
le proxy `/backend`, MongoDB, Ollama et tous les modèles.

Depuis un autre poste, ouvrir
`http://ADRESSE_IP_DE_LA_CIBLE:3001` (`hostname -I` affiche l'adresse).
L'interface écoute par défaut sur `0.0.0.0`; le pare-feu doit autoriser TCP 3001.

Les valeurs par défaut font cohabiter r3 sur le port 3000 et r4 sur le port 3001. La configuration r4 est :

```dotenv
COMPOSE_PROJECT_NAME=ai-for-ssh-v3
WEB_PORT=3001
```

## Garanties du déploiement hors ligne

- Exécuter le paquet depuis un disque local (le répertoire personnel est recommandé), jamais directement depuis `/media` ou `/mnt` avec Docker Snap.
- Aucun accès Internet et aucune commande `apt`, `pip`, `npm`, `docker pull` ou `ollama pull` ne sont nécessaires sur la cible.
- Les modèles ne sont pas téléchargés par `serve.py` : leur magasin complet est embarqué, monté en lecture seule puis vérifié par `ollama list`. Les 12 modèles peuvent être sélectionnés et chargés depuis l'interface.
- Le cross-encoder `bge-reranker-v2-m3` est embarqué localement et la recette `smoke` vérifie son chargement réel sur CPU.
- La première installation recrée uniquement le volume MongoDB du projet, puis restaure `data/mongo.archive`. Cela évite toute réutilisation accidentelle d’un volume MongoDB 8.0.
- `start` vérifie réellement `/`, `/backend/health`, `/backend/api/models` et `/backend/api/settings` depuis la machine hôte avant d’afficher l’URL.
- Le fichier `.env` du bundle est monté dans l’API : les réglages et choix de modèles enregistrés depuis l’interface persistent après recréation des conteneurs.
- `INTERNAL_API_BASE=http://api:8000` est volontaire : Next.js l’utilise côté serveur pour le proxy même origine `/backend`. Il ne faut pas le remplacer par `127.0.0.1`.

Si une tentative antérieure a laissé un volume incompatible ou une installation incomplète :

```bash
bash deploy/offline.sh reinstall
```

`reinstall` supprime le volume MongoDB du projet et restaure le dump embarqué. Sauvegarder les nouvelles données créées sur la cible avant de l’utiliser.

## Exploitation et dépannage

```bash
bash deploy/offline.sh status
bash deploy/offline.sh logs
bash deploy/offline.sh diagnose
bash deploy/offline.sh smoke
bash deploy/offline.sh stop
python3 serve.py
```

En cas d'échec : vérifier `df -h`, `docker system df`, les sommes avec `verify`
et les journaux avec `docker compose logs --tail=100`. Ne jamais poursuivre si
une somme est invalide.
