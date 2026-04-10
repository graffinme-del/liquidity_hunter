# Liquidity Hunter v1 — конфигурация
# Профиль: скальп / импульсы — крутить волатильность в блоке ниже; остальное смягчено в orientation/ и .env.

# Волатильность — баланс: не мёртвые пары, но и не «ноль сигналов в час»
ATR_MIN_PCT_1H = 0.35  # ATR% по 1h — ниже не рассматриваем
ATR_MIN_PCT_15M = 0.28  # ATR% по 15m — импульс «здесь и сейчас»
MIN_PRICE = 0.01  # не торгуем пары с ценой ниже (микрокапы)
ATR_PUMP_BONUS_PCT = 0.5  # выше — бонус к score (pump mode)
# Объём последней свечи vs средний — не дублирует ATR
VOLUME_LAST_MIN_RATIO = 0.55

# Таймфрейм для sweep: "15m" (меньше шума) или "1h" (чище структура, меньше сигналов)
SIGNAL_TIMEFRAME = "15m"
UNIVERSE_TOP_N = 50  # топ по объёму 24h

# liquidity_sweep_reversal — охота на ликвидность, не флет
SWEEP_LOOKBACK = 18
SWEEP_MIN_WICK_TO_BODY = 1.5  # было 2.0 — слишком редко проходило
SWEEP_MIN_WICK_PCT_OF_RANGE = 0.32  # было 0.4 — длинная тень, но не экстрим
SWEEP_RR_MIN = 1.0
SWEEP_CLOSE_POSITION_MIN = 0.5
SWEEP_1H_STRUCTURE = False
SWEEP_OI_MIN_CHANGE_PCT = 0.10  # если OI есть — минимум движения
SWEEP_OI_REQUIRED = False  # импульс может быть без стабильного OI — глобальный ATR режет флет
SWEEP_ATR_MIN_1H = 0.35  # согласовано с ATR_MIN_PCT_1H
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
EXP_ATR_MIN_LEVEL_PCT = 0.25  # только заметное расширение
EXP_ATR_MIN_GROWTH_PCT = 0.1
EXP_VOL_LOOKBACK = 20
EXP_VOL_MULT = 2.0
EXP_OI_MIN_ACCUM_PCT = 3.0
EXP_RR_MIN = 2.0
EXP_BASE_SCORE = 75

# Taker long/short — перекос = подготовка к охоте
TAKER_RATIO_LONG_TRAP = 2.0   # ratio > 2 → много лонгов → бонус к SHORT
TAKER_RATIO_SHORT_TRAP = 0.5  # ratio < 0.5 → много шортов → бонус к LONG
TAKER_TRAP_BONUS = 20

# Дедупликация (мягче — больше шансов поймать серию импульсов)
DEDUP_MINUTES = 30

# Сканер (на слабом VPS частые тики + 50 пар = лаги SSH; поднимай паузы)
TICK_INTERVAL_SEC = 90  # было 60 — меньше нагрузка на сервер
TICK_INTERVAL_NEAR_CLOSE_SEC = 45  # было 15 — не долбить API каждые 15 с
SCAN_SYMBOL_PAUSE_SEC = 0.12  # пауза между парами, разгрузка CPU/сети

# Часы работы (Москва, UTC+3)
TRADING_START_HOUR = 9   # с 9:00
TRADING_END_HOUR = 21    # до 21:00

# Pump screener — отрыв от EMA20 на 1h (поздняя стадия). Для «старта» см. EARLY_PUMP_*.
PUMP_EMA_SCREEN_ENABLED = False  # True — раз в час список «уже оторвались от EMA»
PUMP_EMA_DETACH_PCT_MIN = 20.0   # минимум % выше EMA20 1h
PUMP_EMA_DETACH_PCT_MAX = 35.0   # максимум (сильнее = уже параболика)
PUMP_CHECK_INTERVAL_MIN = 60     # проверка раз в час

# Старт пампа 15m — тихий фон + одна зелёная свеча с всплеском объёма (раньше «догонялок»)
EARLY_PUMP_ENABLED = True
EARLY_PUMP_INTERVAL_MIN = 10
EARLY_PUMP_MAX_SYMBOLS = 200
EARLY_PUMP_MIN_QUOTE_VOL_24H = 25_000.0
EARLY_PUMP_SYMBOL_SORT = "abs_change_24h"
EARLY_PUMP_SHUFFLE = False
EARLY_PUMP_DEDUP_MIN = 30
EARLY_PUMP_QUIET_LOOKBACK = 16       # свечей 15m до импульса — оценка «тишины»
EARLY_PUMP_QUIET_RANGE_MAX = 2.2     # медиана (high-low)/close % в тихом окне
EARLY_PUMP_BODY_MIN_PCT = 1.0
EARLY_PUMP_BODY_MAX_PCT = 7.0        # выше — считаем уже разгон, не «старт»
EARLY_PUMP_VOL_MEDIAN_LOOKBACK = 16
EARLY_PUMP_VOL_SPIKE_MULT = 2.2      # объём последней свечи / медиана объёма до неё
EARLY_PUMP_USE_TAKER = True
EARLY_PUMP_TAKER_MIN_RATIO = 1.02
EARLY_PUMP_TAKER_IGNORE_EMPTY = True

# Импульс 15m — сильный рост за 1–3 свечи (догон может совпадать с поздним этапом)
IMPULSE_15M_ENABLED = False
IMPULSE_15M_MIN_PCT = 6.0        # минимум % роста (от open окна до close последней свечи)
IMPULSE_15M_INTERVAL_MIN = 15    # как часто крутить сканер
IMPULSE_15M_MAX_SYMBOLS = 200
IMPULSE_15M_MIN_QUOTE_VOL_24H = 25_000.0  # ниже порог — больше альтов (шум выше)
IMPULSE_15M_SYMBOL_SORT = "abs_change_24h"  # как у VOL: кто уже шевелится за сутки
IMPULSE_15M_SHUFFLE = False
IMPULSE_15M_DEDUP_MIN = 45       # не дублировать алерт по той же монете, мин
# Объём: средний объём в окне импульса vs средний по свечам ДО окна (база MA)
IMPULSE_15M_USE_VOLUME_MA = True
IMPULSE_15M_VOL_MA_LOOKBACK = 20  # сколько закрытых свечей 15m до импульса для базы
IMPULSE_15M_VOL_MIN_RATIO = 1.25  # (mean vol импульса) / (mean vol базы); 1.8 было слишком жёстко
# Taker: агрегат buyVol/sellVol за k последних интервалов 15m (как окно импульса)
IMPULSE_15M_USE_TAKER = True
IMPULSE_15M_TAKER_MIN_RATIO = 1.01  # чуть выше паритета; 1.08 отсекало почти всё
# Если API не вернул taker — не считать пару «плохой» (иначе ноль сигналов при сбоях/новых парах)
IMPULSE_15M_TAKER_IGNORE_EMPTY = True

# Сканер резкого движения (не топ по объёму — см. VOL_SCAN_SYMBOL_SORT)
VOL_SCAN_ENABLED = True
VOL_SCAN_INTERVAL_MIN = 30       # как часто крутить сканер
VOL_SCAN_MAX_SYMBOLS = 120       # 0 = без лимита (тяжело для VPS)
VOL_SCAN_MIN_QUOTE_VOL_24H = 50_000.0  # отсечь совсем мёртвые пары (USDT за 24h)
VOL_SCAN_SYMBOL_SORT = "abs_change_24h"  # abs_change_24h | low_volume
VOL_SCAN_SHUFFLE = False           # True — перемешать список (другие монеты за проход)
VOL_SCAN_ATR_PCT_MIN = 1.2       # ATR% 15m — выше = резче рынок
VOL_SCAN_ROC_1H_MIN = 2.0        # |изменение цены| за ~1h на 15m свечах, %
VOL_SCAN_RANGE_SPIKE_MULT = 2.0  # диапазон последней свечи vs медиана диапазона
VOL_SCAN_DEDUP_MIN = 45          # не дублировать алерт по той же монете, мин
# «Здесь и сейчас» — не хвост после пампа (ATR уже падает на графике)
VOL_SCAN_REQUIRE_ATR_EXPANDING = True   # ATR% сейчас выше, чем ~1ч назад
VOL_SCAN_ATR_EXPANSION_MIN_RATIO = 1.02 # минимум: ATR_now / ATR_1h_ago (1.02 = +2%)
VOL_SCAN_REJECT_ATR_COOLING = True      # отсекать, если ATR_now < ATR_1h_ago (остывание)
VOL_SCAN_REJECT_STALE_HIGH_ATR = True # высокий ATR, но цена почти не двигается (застой)
VOL_SCAN_STALE_ATR_PCT = 1.5          # выше этого ATR%
VOL_SCAN_STALE_ROC_MAX = 0.4          # и ROC1h ниже — в список не попадаем
