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

# Дедупликация: один и тот же план (символ+лонг/шорт+триггер+стоп+середина TP) не повторять N минут.
# Хранится в data/scanner_dedup.json — общий файл, если запущено несколько процессов бота.
DEDUP_MINUTES = 30

# Сканер охоты за ликвидностью (scanner.py): плановое движение до середины зоны TP от входа, %.
# 5% и выше обычно отсекает почти все сигналы (TP часто 2–4% из-за структуры/ATR) — останутся только пампы/VOL.
# Разумный дефолт 2.5–3; «жёстко без микро» — 4–5. Переопределение: .env SIGNAL_MIN_TP_MOVE_PCT=
SIGNAL_MIN_TP_MOVE_PCT = 2.8

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

# Старт пампа: 5m раньше 15m; незакрытая свеча — ещё раньше (больше ложных)
EARLY_PUMP_ENABLED = True
EARLY_PUMP_TIMEFRAME = "5m"          # 5m | 15m — интервал свечи для «старта»
EARLY_PUMP_INTERVAL_MIN = 8          # реже опрос — меньше спама в TG
EARLY_PUMP_MAX_SYMBOLS = 200
EARLY_PUMP_MIN_QUOTE_VOL_24H = 25_000.0
# Не слать «старт пампа», если за 24h цена уже ушла на ≥ N % (|priceChangePercent|) — меньше догона вершины.
# 0 = выключено.
EARLY_PUMP_SKIP_IF_ABS_CHANGE_24H_PCT = 0.0
EARLY_PUMP_SKIP_24H_IGNORE_EMPTY = True  # если тикер 24h не пришёл — не отсекать пару
EARLY_PUMP_SYMBOL_SORT = "abs_change_24h"
EARLY_PUMP_SHUFFLE = False
EARLY_PUMP_DEDUP_MIN = 20
# «Тишина» — узкие бары перед импульсом (сужает выборку). False = не требовать.
EARLY_PUMP_REQUIRE_QUIET = False
EARLY_PUMP_QUIET_MINUTES = 240       # если REQUIRE_QUIET: сколько минут смотреть «тишину»
EARLY_PUMP_VOL_MEDIAN_MINUTES = 240  # база для медианы объёма до сигнальной свечи
EARLY_PUMP_QUIET_RANGE_MAX = 0.85    # если REQUIRE_QUIET: медиана (high-low)/close % в окне
EARLY_PUMP_BODY_MIN_PCT = 0.35
EARLY_PUMP_BODY_MAX_PCT = 3.0        # выше — уже разгон по одной свече TF
EARLY_PUMP_VOL_SPIKE_MULT = 2.0
# Медиана «объём×цена» по барам до сигнальной (≈ USDT за бар). 0 = выкл. Отсекает пары с крошечной базой и ложными ×30–50.
EARLY_PUMP_MEDIAN_QUOTE_VOL_MIN = 0.0
# Мин. notional сигнальной свечи (close×volume). 0 = выкл.
EARLY_PUMP_SIGNAL_BAR_QUOTE_VOL_MIN = 0.0
# Верхняя граница vol/медиана: слишком большое кратно часто = низкая ликвидность фона или уже разгон. 0 = выкл.
EARLY_PUMP_VOL_RATIO_MAX = 28.0
# Итоговый скоринг 0–100 (см. early_pump_scanner). 0 = не отсекать по баллам; 45–55 заметно сужает выборку.
EARLY_PUMP_MIN_QUALITY_SCORE = 72.0
EARLY_PUMP_MAX_ALERTS_PER_SCAN = 5   # в одном сообщении не больше N пар (0 = без лимита)
EARLY_PUMP_USE_FORMING_CANDLE = True   # сигнал по текущей незакрытой свече (раньше)
EARLY_PUMP_FALLBACK_CLOSED = True      # если формирующая не прошла — последняя закрытая
EARLY_PUMP_FORMING_VOL_RELAX = 0.55    # доля порога vol при неполной свече (× spike)
EARLY_PUMP_USE_TAKER = True
EARLY_PUMP_TAKER_MIN_RATIO = 1.02
EARLY_PUMP_TAKER_IGNORE_EMPTY = True
# Сравнение с BTCUSDT (альт сильнее BTC на той же свече) — выключено
EARLY_PUMP_USE_BTC_FILTER = False  # True — включить фильтр
EARLY_PUMP_MIN_OUTPERFORM_BTC_PCT = 0.25  # тело альта − тело BTCUSDT на последней свече TF, %
# Цена недалеко от EMA (тот же TF): лонг, но не «уже улетели» от средней
EARLY_PUMP_USE_EMA_FILTER = True
EARLY_PUMP_EMA_PERIOD = 20
EARLY_PUMP_MAX_ABOVE_EMA_PCT = 4.0       # (close−EMA)/EMA×100 ≤ этого; <0 отсекаем
EARLY_PUMP_EMA_IGNORE_EMPTY = True
# CVD (прокси): сумма (taker buy − taker sell) по последним барам из klines
EARLY_PUMP_USE_CVD_FILTER = True
EARLY_PUMP_CVD_BARS = 12
EARLY_PUMP_CVD_MIN_SUM = 0.0
EARLY_PUMP_CVD_IGNORE_EMPTY = True
# OI: рост в окне openInterestHist (period = TF)
EARLY_PUMP_USE_OI_FILTER = True
EARLY_PUMP_OI_MIN_CHANGE_PCT = 0.12
EARLY_PUMP_OI_HIST_LIMIT = 8
EARLY_PUMP_OI_IGNORE_EMPTY = True

# Статистика сигналов early (SQLite data/pump_stats.sqlite), оценка через ~24h
PUMP_STATS_ENABLED = True
PUMP_STATS_HIT_MIN_PCT = 5.0   # «взлет»: max за 24h от цены сигнала ≥ этого %
# Автоотчёт в TG (21:00 Мск вместе с дневным отчётом планировщика)
PUMP_STATS_AUTO_REPORT = True

# Фаза 1 / PRE-PUMP — накопление (скан 5m, тихая 15m, см. phase1_accumulation.py)
# По умолчанию ВЫКЛ: сигналы часто не совпадают с реальным «пампом» по времени и засоряют чат.
# Включить: PHASE1_ACCUM_ENABLED=1 в .env
PHASE1_ACCUM_ENABLED = False
PHASE1_INTERVAL_SEC = 180
PHASE1_START_DELAY_SEC = 120
PHASE1_MAX_SYMBOLS = 80
PHASE1_CONCURRENCY = 8
PHASE1_DEDUP_SEC = 7200
# movers = альты с движением за 24h (как VOL); top = только крупный объём (часто 0 сигналов)
PHASE1_SYMBOL_UNIVERSE = "movers"
PHASE1_MIN_QUOTE_VOL_24H = 25_000.0
PHASE1_MIN_SCORE = 70
PHASE1_OI_PERIOD = "5m"
PHASE1_OI_LOOKBACK = 13
PHASE1_RANGE_PCT_MAX = 0.01
PHASE1_RANGE_BARS = 12
PHASE1_LAST_IMPULSE_MAX = 0.010
PHASE1_VOL_HARD_MIN = 1.05
PHASE1_TRAP_BUFFER = 0.002
PHASE1_SKIP_RECENT_MOVE_PCT = 0.07
PHASE1_HOUR_BARS = 12
PHASE1_REQUIRE_15M_QUIET = True
PHASE1_15M_BODY_MAX = 0.008
PHASE1_REQUIRE_CVD_ACCEL = True
PHASE1_REQUIRE_SQUEEZE = True
PHASE1_SQUEEZE_BARS = 3
PHASE1_SQUEEZE_TOP_FRAC = 0.15
PHASE1_USE_MACD_SCORE = True
PHASE1_SCORE_OI_LO = 0.02
PHASE1_SCORE_OI_HI = 0.045
PHASE1_SCORE_VOL_LO = 1.5
PHASE1_SCORE_VOL_HI = 2.5
# Мин. рост OI за окно (доля, 0.005 = 0.5%). 0 = не требовать. Режет «oi=0» в скоринге.
PHASE1_MIN_OI_GROWTH_FRAC = 0.0

# SQUEEZE 5m + OI (отдельный цикл squeeze_oi_scanner.py; по умолчанию выкл.)
# Пороги «узкого боковика» в detectors/squeeze_oi_breakout.py ослаблены vs первую версию;
# точная настройка — через .env (SQUEEZE_OI_MAX_RANGE_PCT и т.д.). Диагностика: SQUEEZE_OI_DEBUG=1.
SQUEEZE_OI_ENABLED = False
SQUEEZE_OI_INTERVAL_SEC = 180
SQUEEZE_OI_START_DELAY_SEC = 90
SQUEEZE_OI_MAX_SYMBOLS = 120  # за один цикл; «шире охват» — 200–300 + movers
SQUEEZE_OI_CONCURRENCY = 6
SQUEEZE_OI_DEDUP_SEC = 3600
SQUEEZE_OI_SYMBOL_UNIVERSE = "movers"  # movers — чаще альты в боковике; top — почти только BTC/ETH
SQUEEZE_OI_MIN_QUOTE_VOL_24H = 25_000.0
SQUEEZE_OI_KLINES_LIMIT = 220
SQUEEZE_OI_HIST_LIMIT = 96
SQUEEZE_OI_COMPRESS_BARS = 36
# Серьёзный режим: обязательный OI и импульс (объём / тело) — меньше шума.
SQUEEZE_OI_REQUIRE_OI = True
SQUEEZE_OI_MIN_OI_GROWTH_PCT = 1.05
SQUEEZE_OI_PRE_BREAKOUT_VOL_BARS = 20
SQUEEZE_OI_BREAKOUT_VOL_MEDIAN_MULT = 1.75
SQUEEZE_OI_BREAKOUT_VOL_2ND_BAR_MULT = 1.35
SQUEEZE_OI_IMPULSE_EACH_BAR_MIN_BODY_PCT = 0.8
SQUEEZE_OI_IMPULSE_TWO_BAR_MOVE_MIN_PCT = 0.8
SQUEEZE_OI_MIN_OI_POINTS = 8
# Сообщения SQUEEZE+OI в TG удаляются через N сек (меньше спама). 0 = не удалять.
SQUEEZE_OI_TELEGRAM_DELETE_AFTER_SEC = 60

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

# Разворот: контекст старшего TF + отказ на младшем (reversal_scanner.py, detectors/reversal_tf_pair.py)
# Разворот: по умолчанию выкл., пока не поставите REVERSAL_ENABLED=1 в .env на сервере
REVERSAL_ENABLED = False
REVERSAL_INTERVAL_SEC = 600
REVERSAL_MAX_ALERTS_PER_SCAN = 5
REVERSAL_START_DELAY_SEC = 180
REVERSAL_MAX_SYMBOLS = 80
REVERSAL_CONCURRENCY = 8
REVERSAL_DEDUP_SEC = 7200
# movers = пары с большим |%24h| (как VOL); top = топ по объёму (BTC/ETH — мало подходящих разворотов)
REVERSAL_SYMBOL_UNIVERSE = "movers"
REVERSAL_MIN_QUOTE_VOL_24H = 25_000.0
REVERSAL_SHORT_ENABLED = True
REVERSAL_LONG_ENABLED = True
REVERSAL_PAIR_4H_1H_ENABLED = True
REVERSAL_PAIR_1H_15M_ENABLED = True
# Контекст 4h (перед шортом — рост; перед лонгом — падение). Было слишком жёстко — не было сигналов неделями.
REVERSAL_CTX_4H_ATR_MIN = 0.35
REVERSAL_CTX_4H_ROC_LOOKBACK = 6
REVERSAL_CTX_4H_ROC_MIN_PCT = 2.2
# Триггер 1h при паре 4h→1h
REVERSAL_TRIG_1H_SWING_LOOKBACK = 18
REVERSAL_TRIG_1H_MIN_RANGE_PCT = 0.08
REVERSAL_TRIG_1H_WICK_MIN = 0.30
# Контекст 1h при паре 1h→15m
REVERSAL_CTX_1H_ATR_MIN = 0.22
REVERSAL_CTX_1H_ROC_LOOKBACK = 12
REVERSAL_CTX_1H_ROC_MIN_PCT = 1.8
# Триггер 15m
REVERSAL_TRIG_15M_SWING_LOOKBACK = 20
REVERSAL_TRIG_15M_MIN_RANGE_PCT = 0.06
REVERSAL_TRIG_15M_WICK_MIN = 0.30
