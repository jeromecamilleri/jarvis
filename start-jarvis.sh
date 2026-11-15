#!/usr/bin/env bash
# Démarre Kensaku (interface graphique, qui lance elle-même son daemon) puis Jarvis, au premier plan.
# Ctrl+C arrête Jarvis uniquement ; Kensaku reste ouvert. Le daemon, son socket et les verrous Tantivy
# ne sont jamais touchés par ce script.
set -u

JARVIS_PORT="${JARVIS_PORT:-8001}"
PORT="$JARVIS_PORT"
KENSAKU_GUI=/usr/bin/kensaku
KENSAKU_CLI="${JARVIS_KENSAKU_CLI:-$HOME/.local/bin/kensaku-cli}"
KENSAKU_WAIT=20   # secondes d'attente maximum du daemon après le lancement de l'interface

erreur() { echo "ERREUR : $*" >&2; exit 1; }

cd "$(dirname "$(readlink -f "$0")")" || erreur "impossible d'entrer dans le répertoire du projet."

[ -x .venv/bin/uvicorn ] || erreur "environnement virtuel absent ou incomplet (.venv/bin/uvicorn introuvable)."

if ss -ltn "sport = :$PORT" | grep -q LISTEN; then
    erreur "le port $PORT est déjà utilisé : Jarvis tourne probablement déjà (aucune seconde instance lancée)."
fi

# Kensaku : l'interface lance son daemon ; on se contente de l'ouvrir si elle n'est pas déjà lancée.
[ -x "$KENSAKU_GUI" ] || erreur "$KENSAKU_GUI introuvable ou non exécutable."
if pgrep -x kensaku >/dev/null; then
    echo "Kensaku est déjà lancé."
else
    echo "Lancement de Kensaku..."
    setsid "$KENSAKU_GUI" >/tmp/kensaku-gui.log 2>&1 < /dev/null &
fi

echo "Attente du daemon Kensaku (${KENSAKU_WAIT}s max)..."
for _ in $(seq "$KENSAKU_WAIT"); do
    pgrep -x kensaku-daemon >/dev/null && break
    sleep 1
done
pgrep -x kensaku-daemon >/dev/null \
    || erreur "le daemon Kensaku n'est pas démarré. Vérifie l'interface Kensaku (journal : /tmp/kensaku-gui.log)."

[ -x "$KENSAKU_CLI" ] || erreur "kensaku-cli introuvable ou non exécutable : $KENSAKU_CLI"
"$KENSAKU_CLI" --help >/dev/null 2>&1 || erreur "kensaku-cli ne répond pas : $KENSAKU_CLI"

echo "Démarrage de Jarvis sur http://127.0.0.1:$PORT (Ctrl+C pour l'arrêter)..."
. .venv/bin/activate
exec uvicorn app.main:app --port "$PORT"
