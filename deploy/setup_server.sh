#!/bin/bash
# Скрипт первичной настройки на сервере
# Запуск: bash setup_server.sh
# Перед запуском: заменить YOUR_USER на своего юзера

set -e
USER="${1:-YOUR_USER}"
DIR="/home/$USER/liquidity_hunter"

echo "Создаю $DIR..."
mkdir -p "$DIR"
cd "$DIR"

if [ -d ".git" ]; then
    echo "Репозиторий уже есть, делаю pull..."
    git pull origin master
else
    echo "Клонирую репозиторий..."
    git clone https://github.com/graffinme-del/liquidity_hunter.git .
fi

echo "Создаю venv..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Создан .env — заполни TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID"
    nano .env
fi

echo "Готово. Дальше:"
echo "  1. Заполни .env"
echo "  2. sudo cp deploy/systemd/liquidity-hunter.service /etc/systemd/system/"
echo "  3. Замени YOUR_USER в unit-файле на $USER"
echo "  4. sudo systemctl daemon-reload && sudo systemctl enable liquidity-hunter && sudo systemctl start liquidity-hunter"
