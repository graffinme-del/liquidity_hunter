# Liquidity Hunter v1 — ТЗ

См. полную спецификацию в репозитории pulse_pilot: `docs/LIQUIDITY_HUNTER_V1_SPEC.md`

Кратко:
- 3 стратегии: liquidity_sweep_reversal, liquidity_sweep_continuation, volatility_expansion
- Все сигналы на русском
- SL по слому тренда (swing, EMA)
- TP — зона вероятной остановки
- Фильтр волатильности: ATR% 1h ≥ 0.25%
