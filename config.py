# Liquidity Hunter v1 — конфигурация

# Волатильность (ОБЯЗАТЕЛЬНЫЙ фильтр)
ATR_MIN_PCT_1H = 0.25  # ниже — не торгуем
ATR_PUMP_BONUS_PCT = 0.5  # выше — бонус к score (pump mode)
VOLUME_LAST_MIN_RATIO = 0.6  # объём последней свечи ≥ avg × это
UNIVERSE_TOP_N = 50  # топ по объёму 24h

# liquidity_sweep_reversal
SWEEP_LOOKBACK = 18
SWEEP_MIN_WICK_TO_BODY = 1.0
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
