# Деплой Liquidity Hunter на сервер

## 1. Создание папки и клонирование

```bash
ssh user@your-server

# Создать папку (подставь свой путь и юзернейм)
mkdir -p ~/liquidity_hunter
cd ~/liquidity_hunter

# Клонировать репо
git clone https://github.com/graffinme-del/liquidity_hunter.git .

# Или если репо уже есть — просто pull
# cd ~/liquidity_hunter && git pull origin master
```

## 2. Виртуальное окружение и зависимости

```bash
cd ~/liquidity_hunter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Конфиг

```bash
cp .env.example .env
nano .env   # или vim
# Заполнить TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
```

## 4. Systemd — автозапуск

Скопировать unit-файл и подставить свой путь/юзернейм:

```bash
# Отредактировать пути в файле
nano deploy/systemd/liquidity-hunter.service
# Заменить YOUR_USER на реального юзера (например pulse или root)
# Заменить /home/YOUR_USER на реальный путь (например /home/pulse)

# Установить сервис
sudo cp deploy/systemd/liquidity-hunter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable liquidity-hunter
sudo systemctl start liquidity-hunter
```

Проверка:
```bash
sudo systemctl status liquidity-hunter
sudo journalctl -u liquidity-hunter -f
```

## 5. Обновление после изменений

**Локально (на ПК):**
```bash
cd c:\Users\job\Desktop\liquidity_hunter
git add .
git commit -m "описание изменений"
git push origin master
```

**На сервере:**
```bash
ssh user@your-server
cd ~/liquidity_hunter
git pull origin master
sudo systemctl restart liquidity-hunter
```

Проверка: `sudo systemctl status liquidity-hunter`

## 6. Статистика и отчёты

- **Сигналы** логируются в `storage/signals.jsonl`
- **Ежедневный отчёт** в 21:00 Москва — автоматически (встроен в бота)
- Отчёт: TP/SL/NO_OUTCOME, winrate, по стратегиям

Ручной резолв (если нужно):
```bash
cd ~/liquidity_hunter && source venv/bin/activate
python outcome_resolver.py --window-hours 48
```
