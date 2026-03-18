# Liquidity Hunter v1 — конфигурация

# Волатильность (ОБЯЗАТЕЛЬНЫЙ фильтр)
ATR_MIN_PCT_1H = 0.3  # ниже — не торгуем (ослаблено с 0.4 для большего потока)
MIN_PRICE = 0.01  # не торгуем пары с ценой ниже (микрокапы)
ATR_PUMP_BONUS_PCT = 0.5  # выше — бонус к score (pump mode)
VOLUME_LAST_MIN_RATIO = 0.8  # объём последней свечи ≥ avg × это (ослаблено с 1.0)

# Таймфрейм для sweep: "15m" (меньше шума) или "1h" (чище структура, меньше сигналов)
SIGNAL_TIMEFRAME = "15m"
UNIVERSE_TOP_N = 50  # топ по объёму 24h

# liquidity_sweep_reversal — охота на ликвидность, не флет
SWEEP_LOOKBACK = 18
SWEEP_MIN_WICK_TO_BODY = 1.0
SWEEP_RR_MIN = 1.0
SWEEP_CLOSE_POSITION_MIN = 0.5
SWEEP_1H_STRUCTURE = False
SWEEP_OI_MIN_CHANGE_PCT = 1.0  # OI должен двигаться — иначе нет ликвидаций для охоты
SWEEP_ATR_MIN_1H = 0.5  # sweep только при реальном движении (не флет)
SWEEP_MIN_CANDLES = 25
SWEEP_RR_TARGET = 1.8
SWEEP_BASE_SCORE = 70

# liquidity_sweep_continuation
CONT_RANGE_LOOKBACK = 12
CONT_FAKE_SWEEP_BARS = 3
CONT_VOLUME_MULT = 1.5
CONT_BODY_MIN_PCT = 0.3
CONT_RR_TARGET = 2.2
CONT_BASE_SCORE = 75

# volatility_expansion
EXP_RANGE_LOOKBACK = 10
EXP_MAX_RANGE_PCT = 3.0
EXP_ATR_LOOKBACK = 14
EXP_ATR_COOLDOWN = 4
EXP_ATR_MIN_LEVEL_PCT = 0.2
EXP_ATR_MIN_GROWTH_PCT = 0.1
EXP_VOL_LOOKBACK = 20
EXP_VOL_MULT = 2.0
EXP_OI_MIN_ACCUM_PCT = 3.0
EXP_RR_MIN = 2.0
EXP_BASE_SCORE = 75

# Дедупликация
DEDUP_MINUTES = 60

# Сканер
TICK_INTERVAL_SEC = 60  # интервал тика
TICK_INTERVAL_NEAR_CLOSE_SEC = 15  # ближе к закрытию свечи — чаще (для быстрой реакции)

# Часы работы (Москва, UTC+3)
TRADING_START_HOUR = 9   # с 9:00
TRADING_END_HOUR = 21    # до 21:00

# Pump screener (python -m pump_screener [--tg])
PUMP_EMA_DETACH_PCT_MIN = 20.0   # минимум % выше EMA20 1h
PUMP_EMA_DETACH_PCT_MAX = 35.0   # максимум (сильнее = уже параболика)
