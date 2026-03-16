# Liquidity Hunter v1

Новый Telegram-бот для сигналов на фьючерсах Binance USDT-M. Фокус на ликвидности и волатильности.

## Принципы

- **Ликвидность:** крупный игрок собирает стопы, потом везёт цену. Играем на стороне охоты или разворота после охоты.
- **Волатильность — фильтр:** только монеты с активной торговлей (ATR% 1h ≥ 0.25%).

## Стратегии

1. **liquidity_sweep_reversal** — вынос high/low + возврат внутрь → вход в сторону возврата
2. **liquidity_sweep_continuation** — ложный вынос + импульс в другую сторону
3. **volatility_expansion** — сжатие → расширение ATR + объёмный пробой

## Установка

```bash
cd liquidity_hunter
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env
# Заполнить TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env
```

## Запуск

```bash
python main.py
```

## Структура

```
liquidity_hunter/
├── config.py
├── structure.py       # swing, EMA, SL/TP
├── data/
│   └── binance_client.py
├── detectors/
│   ├── liquidity_sweep_reversal.py
│   ├── liquidity_sweep_continuation.py
│   └── volatility_expansion.py
├── scanner.py
├── notifier.py
└── main.py
```

## Формат сигнала (русский)

```
🟢 LONG  BTCUSDT

Почему: Цена вынесла лоу, вернулась внутрь — ждём отскок вверх

Вход: когда цена достигнет 95 200 — покупай по рынку
Стоп: 94 500 — тренд сломается, если цена уйдёт туда
Цель: зона 96 800–97 200 — туда вероятно дойдёт

RR: 1.8 | ATR%: 0.4%
```
