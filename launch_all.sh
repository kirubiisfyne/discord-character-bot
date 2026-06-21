#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# launch_all.sh — Start all characters in separate Terminal windows
# Usage: bash launch_all.sh
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv/bin/activate"

# List your character folders here
CHARACTERS=("characters/alex" "characters/sooyensu" "characters/aria")

for char in "${CHARACTERS[@]}"; do
  CHAR_NAME=$(basename "$char")
  CHAR_PATH="$SCRIPT_DIR/$char"

  # Check that .env and character.json exist
  if [ ! -f "$CHAR_PATH/.env" ]; then
    echo "⚠️  Skipping $CHAR_NAME — missing .env (copy from .env.example and fill in token)"
    continue
  fi
  if [ ! -f "$CHAR_PATH/character.json" ]; then
    echo "⚠️  Skipping $CHAR_NAME — missing character.json"
    continue
  fi

  echo "🚀 Launching $CHAR_NAME..."

  # Open a new Terminal window for each character (no Accessibility permission needed)
  osascript -e "tell application \"Terminal\" to do script \"source '$VENV' && python '$SCRIPT_DIR/bot.py' --character '$CHAR_PATH'\""

  sleep 0.5
done

echo "✅ All characters launched."
