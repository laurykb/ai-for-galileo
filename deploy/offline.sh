#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OFFLINE_INSTALLED="$ROOT_DIR/.offline-installed"
ACTION="${1:-help}"
shift || true

# Toutes les actions (start, smoke, diagnose incluses) doivent retrouver le
# mode sélectionné lors de l'installation et les modèles personnalisés.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

usage() {
  cat <<'EOF'
AI for SSH v3 — déploiement hors ligne

Machine connectée : deploy/offline.sh prepare DESTINATION
Machine hors ligne : python3 serve.py
Exploitation : status | logs | stop | diagnose | smoke | reinstall
EOF
}

require_docker() {
  command -v docker >/dev/null || { echo "Docker Engine est requis." >&2; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo "Docker Compose est requis." >&2; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Le daemon Docker est inaccessible." >&2; exit 1; }
}

ensure_image() { docker image inspect "$1" >/dev/null 2>&1 || docker pull "$1"; }

wait_service() {
  local service="$1" limit="${2:-180}" elapsed=0 id state
  while (( elapsed < limit )); do
    id="$(docker compose ps -q "$service")"
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || true)"
    case "$state" in
      healthy|running) return 0 ;;
      unhealthy|exited|dead) docker compose logs --tail=100 "$service"; return 1 ;;
    esac
    sleep 2; elapsed=$((elapsed + 2))
  done
  echo "$service n'est pas prêt après ${limit}s." >&2; return 1
}

find_ollama_store() {
  local first_model from_path candidate
  if [[ -n "${OLLAMA_MODELS_DIR:-}" ]]; then
    candidate="$OLLAMA_MODELS_DIR"
  else
    first_model="$(ollama list | awk 'NR == 2 {print $1}')"
    [[ -n "$first_model" ]] || { echo "Aucun modèle Ollama à exporter." >&2; return 1; }
    from_path="$(ollama show "$first_model" --modelfile | awk '$1 == "FROM" && $2 ~ /^\// {print $2; exit}')"
    candidate="${from_path%/blobs/*}"
  fi
  [[ -d "$candidate/blobs" && -d "$candidate/manifests" ]] || {
    echo "Magasin Ollama introuvable. Définissez OLLAMA_MODELS_DIR." >&2
    return 1
  }
  printf '%s\n' "$candidate"
}

validate_bundle() {
  local item
  for item in images.tar compose.yaml compose.host-ollama.yaml .env.compose.example serve.py deploy/offline.sh \
    docs/INSTALLATION_HORS_LIGNE.md models/ollama-models.tsv data/mongo.archive SHA256SUMS; do
    [[ -s "$ROOT_DIR/$item" ]] || { echo "Fichier obligatoire absent : $item" >&2; return 1; }
  done
}

verify() {
  require_docker; validate_bundle
  [[ -s "$ROOT_DIR/models/bge-reranker-v2-m3/model.safetensors" ]] || { echo "Cross-encoder absent du bundle." >&2; return 1; }
  (cd "$ROOT_DIR" && sha256sum -c --quiet SHA256SUMS)
  echo "Paquet complet et sommes SHA-256 valides."
}

prepare() {
  local destination="${1:-}" model image_archive ollama_store mongo_db
  [[ -n "$destination" ]] || { echo "Destination requise." >&2; usage; exit 2; }
  [[ "$(uname -s)/$(uname -m)" == "Linux/x86_64" ]] || { echo "Linux x86_64 requis." >&2; exit 1; }
  [[ ! -e "$destination" ]] || { echo "La destination existe déjà : $destination" >&2; exit 1; }
  require_docker
  command -v ollama >/dev/null || { echo "Ollama est requis sur la source." >&2; exit 1; }
  [[ -s "$ROOT_DIR/models/bge-reranker-v2-m3/model.safetensors" ]] || { echo "Cross-encoder local absent de la source." >&2; exit 1; }

  echo "[1/5] Validation de l'application"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/validate_export.py"
  echo "[2/5] Construction des images runtime"
  docker build -t ai-for-ssh-v3-api:latest "$ROOT_DIR"
  docker build -t ai-for-ssh-v3-web:latest "$ROOT_DIR/web"
  ensure_image mongo:7.0; ensure_image ollama/ollama:latest

  echo "[3/5] Création du dossier transférable"
  mkdir -p "$destination/deploy" "$destination/docs/PDF" "$destination/docs/out" \
    "$destination/docs/PDF_TO" "$destination/data" "$destination/models"
  cp "$ROOT_DIR/compose.yaml" "$ROOT_DIR/compose.host-ollama.yaml" "$ROOT_DIR/.env.compose.example" "$ROOT_DIR/serve.py" "$destination/"
  cp "$ROOT_DIR/deploy/offline.sh" "$destination/deploy/"
  cp "$ROOT_DIR/docs/INSTALLATION_HORS_LIGNE.md" "$destination/docs/"
  find "$ROOT_DIR/data" -mindepth 1 -maxdepth 1 ! -name mongodb \
    -exec cp -a {} "$destination/data/" \;
  mongo_db="$(awk -F= '$1 == "MONGO_DB" {value=$2} END {print value}' "$ROOT_DIR/.env")"
  [[ -n "$mongo_db" ]] || { echo "MONGO_DB absent du fichier .env source." >&2; return 1; }
  echo "Sauvegarde MongoDB : $mongo_db"
  docker run --rm --network host mongo:7.0 mongodump \
    --host "${MONGO_SOURCE_HOST:-127.0.0.1}" \
    --port "${MONGO_SOURCE_PORT:-27017}" \
    --db "$mongo_db" --archive > "$destination/data/mongo.archive"
  [[ -s "$destination/data/mongo.archive" ]] || { echo "Sauvegarde Mongo vide." >&2; return 1; }

  cp -a "$ROOT_DIR/docs/PDF/." "$destination/docs/PDF/" 2>/dev/null || true
  cp -a "$ROOT_DIR/docs/out/." "$destination/docs/out/" 2>/dev/null || true
  cp -a "$ROOT_DIR/docs/PDF_TO/." "$destination/docs/PDF_TO/" 2>/dev/null || true
  mkdir -p "$ROOT_DIR/dist"
  image_archive="$(mktemp "$ROOT_DIR/dist/.offline-images.XXXXXX.tar")"
  trap 'rm -f "$image_archive"' RETURN
  docker save -o "$image_archive" ai-for-ssh-v3-api:latest ai-for-ssh-v3-web:latest mongo:7.0 ollama/ollama:latest
  cp "$image_archive" "$destination/images.tar"

  echo "[4/5] Copie fidèle de tous les modèles Ollama"
  ollama_store="$(find_ollama_store)"
  : > "$destination/models/ollama-models.tsv"
  while IFS= read -r model; do
    [[ -n "$model" ]] && printf '%s\n' "$model" >> "$destination/models/ollama-models.tsv"
  done < <(ollama list | awk 'NR > 1 {print $1}')
  cp -a "$ROOT_DIR/models/bge-reranker-v2-m3" "$destination/models/"
  mkdir -p "$destination/models/ollama-store"
  cp -a "$ollama_store/." "$destination/models/ollama-store/"

  echo "[5/5] Contrôle d'intégrité"
  (cd "$destination" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/audit_export.py" "$destination" --report "$destination/OFFLINE_AUDIT.json"
  (cd "$destination" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
  echo "Export prêt : $destination ($(du -sh "$destination" | awk '{print $1}'))"
}

gpu_runtime_available() {
  docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'
}

host_ollama_available() {
  python3 - <<'PY'
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open("http://127.0.0.1:11434/api/tags", timeout=3) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
}

host_ollama_models() {
  python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:11434/api/tags", timeout=5) as response:
    payload = json.load(response)
for item in payload.get("models", []):
    name = item.get("name") or item.get("model")
    if name:
        print(name)
PY
}

required_models() {
  local generation="${GEN_MODEL:-llama3.2:3b}" var value
  printf '%s\n' "${EMBED_MODEL:-bge-m3:latest}" "$generation" "${LYNX_MODEL:-$generation}"
  for var in REWRITER_MODEL AGENT_MODEL PLANNER_MODEL EXTRACTION_MODEL SYNTHESIS_MODEL JUDGE_MODEL ENHANCEMENT_MODEL; do
    value="${!var:-$generation}"
    [[ -n "$value" ]] && printf '%s\n' "$value"
  done
}

host_has_required_models() {
  local installed expected
  installed="$(host_ollama_models 2>/dev/null)" || return 1
  while IFS= read -r expected; do
    grep -Fxq "$expected" <<<"$installed" || return 1
  done < <(required_models | sort -u)
}

using_host_ollama() {
  [[ "${HOST_OLLAMA_MODE:-0}" == "1" ]] ||
    grep -q '^COMPOSE_FILE=compose.host-ollama.yaml$' "$ROOT_DIR/.env" 2>/dev/null
}

configure_ollama_runtime() {
  local requested="${OLLAMA_DEPLOYMENT_MODE:-auto}" selected missing="" expected installed
  sed -i '/^COMPOSE_FILE=compose\.host-ollama\.yaml$/d; /^HOST_OLLAMA_MODE=1$/d; /^OLLAMA_ACTIVE_MODE=/d; /^OLLAMA_DOCKER_RUNTIME=/d' .env
  unset COMPOSE_FILE HOST_OLLAMA_MODE OLLAMA_ACTIVE_MODE OLLAMA_DOCKER_RUNTIME
  case "$requested" in
    auto)
      if host_ollama_available && host_has_required_models; then selected=host
      elif gpu_runtime_available; then selected=docker-gpu
      else selected=docker-cpu
      fi
      ;;
    docker-gpu)
      gpu_runtime_available || { echo "OLLAMA_DEPLOYMENT_MODE=docker-gpu demandé, mais le runtime Docker NVIDIA est absent." >&2; return 1; }
      selected=docker-gpu ;;
    host)
      host_ollama_available || { echo "OLLAMA_DEPLOYMENT_MODE=host demandé, mais Ollama hôte est injoignable." >&2; return 1; }
      if ! host_has_required_models; then
        installed="$(host_ollama_models 2>/dev/null || true)"
        while IFS= read -r expected; do
          grep -Fxq "$expected" <<<"$installed" || missing="${missing}${missing:+, }$expected"
        done < <(required_models | sort -u)
        echo "Ollama hôte ne contient pas les modèles actifs : $missing" >&2
        echo "Utilisez OLLAMA_DEPLOYMENT_MODE=auto pour le repli local sans téléchargement." >&2
        return 1
      fi
      selected=host ;;
    docker-cpu) selected=docker-cpu ;;
    *) echo "OLLAMA_DEPLOYMENT_MODE invalide : $requested (auto, docker-gpu, host ou docker-cpu)." >&2; return 2 ;;
  esac
  case "$selected" in
    host)
      printf '\nCOMPOSE_FILE=compose.host-ollama.yaml\nHOST_OLLAMA_MODE=1\nOLLAMA_ACTIVE_MODE=host\n' >> .env
      export COMPOSE_FILE=compose.host-ollama.yaml HOST_OLLAMA_MODE=1 OLLAMA_ACTIVE_MODE=host
      echo "Mode Ollama retenu : serveur hôte (modèles actifs présents)." ;;
    docker-gpu)
      printf '\nOLLAMA_ACTIVE_MODE=docker-gpu\nOLLAMA_DOCKER_RUNTIME=nvidia\n' >> .env
      export OLLAMA_ACTIVE_MODE=docker-gpu OLLAMA_DOCKER_RUNTIME=nvidia
      echo "Mode Ollama retenu : conteneur avec runtime NVIDIA." ;;
    docker-cpu)
      printf '\nOLLAMA_ACTIVE_MODE=docker-cpu\nOLLAMA_DOCKER_RUNTIME=runc\n' >> .env
      export OLLAMA_ACTIVE_MODE=docker-cpu OLLAMA_DOCKER_RUNTIME=runc
      echo "Mode Ollama retenu : conteneur CPU avec magasin embarqué (aucun téléchargement)." ;;
  esac
}

verify_gpu_runtime() {
  if using_host_ollama; then
    if command -v nvidia-smi >/dev/null; then
      nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
    else
      echo "Ollama hôte prêt ; nvidia-smi absent, accélérateur non vérifiable."
    fi
  elif [[ "${OLLAMA_ACTIVE_MODE:-}" == "docker-cpu" ]]; then
    echo "Ollama Docker fonctionne en mode CPU."
  else
    docker compose exec -T ollama nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
  fi
}

verify_models() {
  local installed expected missing="" count=0
  if using_host_ollama; then
    installed="$(host_ollama_models)"
  else
    installed="$(docker compose exec -T ollama ollama list </dev/null | awk 'NR > 1 {print $1}')"
  fi
  while IFS= read -r expected; do
    if grep -Fxq "$expected" <<<"$installed"; then count=$((count + 1))
    else missing="${missing}${missing:+, }$expected"
    fi
  done < <(required_models | sort -u)
  [[ -z "$missing" ]] || { echo "Modèles actifs absents (${OLLAMA_ACTIVE_MODE:-inconnu}) : $missing" >&2; return 1; }
  echo "Modèles actifs prêts : $count modèle(s) unique(s), mode ${OLLAMA_ACTIVE_MODE:-inconnu}."
}

ensure_selected_ollama_available() {
  if using_host_ollama; then
    host_ollama_available || {
      echo "Le déploiement utilise Ollama hôte, mais http://127.0.0.1:11434 est injoignable." >&2
      echo "Démarrez Ollama, ou lancez reinstall avec OLLAMA_DEPLOYMENT_MODE=auto." >&2
      return 1
    }
    host_has_required_models || {
      echo "L’Ollama hôte ne contient plus tous les modèles actifs. Lancez diagnose." >&2
      return 1
    }
  else
    docker image inspect ollama/ollama:latest >/dev/null 2>&1 || {
      echo "Image locale ollama/ollama:latest absente ; lancez reinstall depuis le bundle complet." >&2
      return 1
    }
  fi
}

show_embedded_models() {
  local count model
  count="$(awk "NF {count++} END {print count+0}" "$ROOT_DIR/models/ollama-models.tsv")"
  [[ "$count" -gt 0 ]] || { echo "Aucun modèle Ollama dans le paquet." >&2; return 1; }
  [[ -d "$ROOT_DIR/models/ollama-store/blobs" && -d "$ROOT_DIR/models/ollama-store/manifests" ]] || {
    echo "Magasin Ollama embarqué incomplet." >&2; return 1;
  }
  echo "Installation locale des modèles embarqués : $count modèle(s), aucun accès Internet."
  while IFS= read -r model; do [[ -z "$model" ]] || echo "  - $model"; done < "$ROOT_DIR/models/ollama-models.tsv"
}

bundle_mongo_db() {
  awk -F= '$1 == "MONGO_DB" {value=$2} END {print value}' "$ROOT_DIR/.env"
}

restore_mongo() {
  local mongo_db
  mongo_db="$(bundle_mongo_db)"
  [[ -n "$mongo_db" ]] || { echo "MONGO_DB absent du fichier .env." >&2; return 1; }
  docker compose exec -T mongo mongorestore --archive --drop < "$ROOT_DIR/data/mongo.archive"
}

verify_mongo() {
  local mongo_db collections
  mongo_db="$(bundle_mongo_db)"
  collections="$(docker compose exec -T mongo mongosh --quiet "$mongo_db" \
    --eval 'db.getCollectionNames().length' </dev/null)"
  [[ "$collections" =~ ^[1-9][0-9]*$ ]] || {
    echo "MongoDB restauré sans collection ($mongo_db)." >&2; return 1;
  }
}

reset_installation_state() {
  echo "Initialisation du volume MongoDB 7.0 propre..."
  docker compose down --volumes --remove-orphans
}

published_web_port() {
  local published
  published="$(docker compose port web 3000)"
  printf "%s\n" "${published##*:}"
}

verify_host_http() {
  local port="$1"
  TEST_WEB_PORT="$port" python3 - <<PY
import os
import urllib.request

port = os.environ["TEST_WEB_PORT"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for path in ("/", "/backend/health", "/backend/api/models", "/backend/api/settings"):

    url = f"http://127.0.0.1:{port}{path}"
    try:
        with opener.open(url, timeout=15) as response:
            if response.status != 200:
                raise SystemExit(f"{url}: HTTP {response.status}")
    except Exception as exc:
        raise SystemExit(
            "Le service web est sain dans Docker mais ne répond pas depuis "
            f"hôte ({url}) : {exc}"
        ) from exc
print(f"Interface web prête : http://localhost:{port}")
PY
}

verify_model_runtime() {
  docker compose exec -T api python - <<'PY'
from env_config import CE_DEVICE, CROSS_ENCODER_LOCAL_PATH
from retrieval.cross_encoder import _load_cross_encoder_local

model = _load_cross_encoder_local(CROSS_ENCODER_LOCAL_PATH, CE_DEVICE)
print(f"Cross-encoder prêt : {CROSS_ENCODER_LOCAL_PATH} ({CE_DEVICE}).")
del model
PY
  docker compose exec -T api python - <<PY
import os
import requests

host = os.environ["OLLAMA_HOST"].rstrip("/")
checks = (
    ("embedding", "/api/embed", {"model": os.environ["EMBED_MODEL"], "input": "test", "keep_alive": 0}),
    ("génération", "/api/generate", {"model": os.environ["GEN_MODEL"], "prompt": "Réponds OK", "stream": False, "keep_alive": 0, "options": {"num_ctx": 2048, "num_predict": 1}}),
)
for label, path, payload in checks:
    response = requests.post(host + path, json=payload, timeout=600)
    response.raise_for_status()
    print(f"Test Ollama {label} réussi ({payload['model']}).")
PY
}

ensure_local_install_path() {
  case "$ROOT_DIR/" in
    /media/*|/mnt/*)
      echo "Installation refusée depuis un support monté : $ROOT_DIR" >&2
      echo "Copiez le paquet sur le disque local puis relancez :" >&2
      echo '  cp -a "/chemin/du/support/bundle" "$HOME/ai-for-ssh-offline"' >&2
      echo '  cd "$HOME/ai-for-ssh-offline" && python3 serve.py' >&2
      return 1
      ;;
  esac
}

install_bundle() {
  local requested_project="${COMPOSE_PROJECT_NAME:-}" requested_web_port="${WEB_PORT:-}"
  local requested_mode="${OLLAMA_DEPLOYMENT_MODE:-}" requested_runtime="${OLLAMA_DOCKER_RUNTIME:-}"
  ensure_local_install_path
  [[ ! -f "$OFFLINE_INSTALLED" ]] || {
    echo "Installation déjà effectuée. Utilisez start, ou reinstall pour restaurer le dump initial." >&2
    return 1
  }
  verify
  show_embedded_models
  cd "$ROOT_DIR"
  docker load < images.tar
  local image
  for image in ai-for-ssh-v3-api:latest ai-for-ssh-v3-web:latest mongo:7.0 ollama/ollama:latest; do
    docker image inspect "$image" >/dev/null
  done
  [[ -f .env ]] || cp .env.compose.example .env
  chmod 0644 .env
  set -a
  source .env
  set +a
  [[ -z "$requested_project" ]] || export COMPOSE_PROJECT_NAME="$requested_project"
  [[ -z "$requested_web_port" ]] || export WEB_PORT="$requested_web_port"
  [[ -z "$requested_mode" ]] || export OLLAMA_DEPLOYMENT_MODE="$requested_mode"
  [[ -z "$requested_runtime" ]] || export OLLAMA_DOCKER_RUNTIME="$requested_runtime"
  configure_ollama_runtime
  docker compose config --quiet
  reset_installation_state
  if using_host_ollama; then
    docker compose up -d mongo
  else
    docker compose up -d mongo ollama
  fi
  using_host_ollama || wait_service ollama
  verify_gpu_runtime
  wait_service mongo
  restore_mongo
  verify_mongo
  verify_models
  touch .offline-installed
  echo "Installation terminée. Lancez : python3 serve.py"
}

start_bundle() {
  local port
  require_docker; cd "$ROOT_DIR"
  [[ -f .offline-installed ]] || { echo "Lancez d'abord deploy/offline.sh install." >&2; exit 1; }
  ensure_selected_ollama_available
  docker compose config --quiet
  docker compose up -d --no-build
  wait_service web 240
  port="$(published_web_port)"
  verify_host_http "$port"
}

smoke_bundle() {
  local published port
  require_docker
  cd "$ROOT_DIR"
  [[ -f .offline-installed ]] || { echo "Installation hors ligne non effectuée." >&2; return 1; }
  ensure_selected_ollama_available
  docker compose config --quiet
  docker compose up -d --no-build
  wait_service mongo
  using_host_ollama || wait_service ollama
  verify_gpu_runtime
  wait_service api 240
  wait_service web 240
  verify_models
  verify_model_runtime
  verify_mongo
  docker compose exec -T web node -e \
    "Promise.all([process.env.INTERNAL_API_BASE+'/health','http://127.0.0.1:3000/backend/health'].map(async u=>{const r=await fetch(u);if(!r.ok)throw new Error(u+' HTTP '+r.status)}))"
  published="$(docker compose port web 3000)"
  port="${published##*:}"
  TEST_WEB_PORT="$port" python3 - <<'PY'
import os
import urllib.request
port = os.environ["TEST_WEB_PORT"]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for path in ("/", "/backend/health"):
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
        if response.status != 200:
            raise SystemExit(f"{path}: HTTP {response.status}")
print(f"Recette HTTP réussie sur http://localhost:{port}")
PY
  verify_host_http "$port"
}

reinstall_bundle() {
  cd "$ROOT_DIR"
  rm -f .offline-installed
  install_bundle
  start_bundle
}

diagnose_bundle() {
  cd "$ROOT_DIR"
  echo "=== Déploiement ==="
  echo "Racine: $ROOT_DIR"
  echo "Architecture: $(uname -s)/$(uname -m)"
  echo "Mode Ollama demandé: ${OLLAMA_DEPLOYMENT_MODE:-auto}"
  echo "Mode Ollama actif: ${OLLAMA_ACTIVE_MODE:-non installé}"
  echo "Projet Compose: ${COMPOSE_PROJECT_NAME:-ai-for-ssh-v3}"
  echo "Port web: ${WEB_PORT:-3001}"
  df -h "$ROOT_DIR" 2>&1 || true
  echo "=== Docker ==="
  docker info --format 'Runtimes: {{json .Runtimes}}' 2>&1 || true
  docker compose config --quiet 2>&1 && echo "Configuration Compose valide." || true
  echo "=== GPU hôte ==="
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>&1 || true
  echo "=== Images requises ==="
  local image
  for image in ai-for-ssh-v3-api:latest ai-for-ssh-v3-web:latest mongo:7.0 ollama/ollama:latest; do
    docker image inspect "$image" --format '{{.RepoTags}}' 2>/dev/null || echo "ABSENTE: $image"
  done
  echo "=== Ollama hôte ==="
  if host_ollama_available; then
    echo "Joignable sur 127.0.0.1:11434."
    host_ollama_models 2>&1 || true
  else
    echo "Injoignable sur 127.0.0.1:11434."
  fi
  echo "=== Modèles actifs ==="
  required_models | sort -u
  echo "=== Services ==="
  docker compose ps -a 2>&1 || true
  docker compose logs --tail=100 mongo ollama api web 2>&1 || true
}

case "$ACTION" in
  prepare) prepare "${1:-}" ;;
  verify) verify ;;
  install) install_bundle ;;
  start) start_bundle ;;
  smoke) smoke_bundle ;;
  reinstall) reinstall_bundle ;;
  status) cd "$ROOT_DIR"; docker compose ps ;;
  logs) cd "$ROOT_DIR"; docker compose logs -f "$@" ;;
  stop) cd "$ROOT_DIR"; docker compose down ;;
  diagnose) diagnose_bundle ;;
  help|-h|--help) usage ;;
  *) echo "Action inconnue : $ACTION" >&2; usage >&2; exit 2 ;;
esac
