#!/usr/bin/env bash
# Установка в уже склонированном каталоге проекта. Секреты не запрашиваются.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(pwd -P)}"
if [[ ! -f "$PROJECT_DIR/pyproject.toml" ]]; then
  echo "Запустите скрипт из каталога HHRaiser или задайте PROJECT_DIR." >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "Запустите установщик через sudo." >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  apt-get update
  apt-get install -y curl ca-certificates
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
fi

if ! command -v task >/dev/null 2>&1; then
  apt-get update
  apt-get install -y curl ca-certificates
  curl -1sLf 'https://dl.cloudsmith.io/public/task/task/setup.deb.sh' | bash
  apt-get update
  apt-get install -y task
fi

cd "$PROJECT_DIR"
uv sync --locked --extra dev
uv run playwright install-deps chromium
PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/state/main/playwright-browsers" \
  uv run playwright install chromium
uv run hhraiser setup

escaped_project_dir=$(printf '%s' "$PROJECT_DIR" | sed 's/[&|]/\\&/g')
sed "s|@PROJECT_DIR@|$escaped_project_dir|g" deploy/systemd/hhraiser@.service \
  > /etc/systemd/system/hhraiser@.service
sed "s|@PROJECT_DIR@|$escaped_project_dir|g" deploy/systemd/hhraiser-bot.service \
  > /etc/systemd/system/hhraiser-bot.service
systemctl daemon-reload
echo "Установка завершена. Запустите службы: task server-start"
