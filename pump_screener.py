"""
Скринер пампов: монеты, где цена оторвалась от EMA20 на 20–25%+.
Не сигналы на вход — список уже оторвавшихся монет (для откатов, наблюдения и т.п.).
"""
import asyncio
import os

import aiohttp

import config
from data.binance_client import BinanceClient
from structure import ema20




def _to_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


async def run_screener(send_tg: bool = False) -> list[dict]:
    """
    Возвращает список монет с пампом (цена 20–35% выше EMA20 1h).
    Каждый элемент: {symbol, close, ema20, detach_pct, volume_24h}
    """
    from dotenv import load_dotenv
    load_dotenv()

    pumped: list[dict] = []

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(session)
        symbols = await client.get_top_symbols(config.UNIVERSE_TOP_N)

        for symbol in symbols:
            try:
                candles_1h = await client.get_klines(symbol, "1h", 50)
                if len(candles_1h) < 25:
                    continue

                last = candles_1h[-1]
                close = _to_float(last.get("close"))
                if close < config.MIN_PRICE:
                    continue

                ema_val = ema20(candles_1h)
                if ema_val is None or ema_val <= 0:
                    continue

                detach_pct = (close - ema_val) / ema_val * 100

                if config.PUMP_EMA_DETACH_PCT_MIN <= detach_pct <= config.PUMP_EMA_DETACH_PCT_MAX:
                    pumped.append({
                        "symbol": symbol,
                        "close": close,
                        "ema20": ema_val,
                        "detach_pct": round(detach_pct, 1),
                        "volume_24h": None,  # опционально
                    })
            except Exception as e:
                print(f"[PUMP] {symbol}: {e}")
                continue
            finally:
                pause = getattr(config, "SCAN_SYMBOL_PAUSE_SEC", 0) or 0
                if pause > 0:
                    await asyncio.sleep(pause)

    # Сортируем по отрыву (сильнее = выше)
    pumped.sort(key=lambda x: x["detach_pct"], reverse=True)

    if send_tg and pumped:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if token and chat_id:
            mn, mx = config.PUMP_EMA_DETACH_PCT_MIN, config.PUMP_EMA_DETACH_PCT_MAX
            lines = [f"<b>Пампы ({mn:.0f}-{mx:.0f}% выше EMA20 1h)</b>"]
            for p in pumped[:15]:
                lines.append(f"  {p['symbol']}: +{p['detach_pct']}% от EMA")
            text = "\n".join(lines)
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}) as r:
                        pass
            except Exception as e:
                print(f"[PUMP] TG: {e}")

    return pumped


def main():
    import sys
    send_tg = "--tg" in sys.argv
    pumped = asyncio.run(run_screener(send_tg=send_tg))
    if not pumped:
        print(f"Пампов ({config.PUMP_EMA_DETACH_PCT_MIN:.0f}-{config.PUMP_EMA_DETACH_PCT_MAX:.0f}% от EMA) нет.")
        return

    mn, mx = config.PUMP_EMA_DETACH_PCT_MIN, config.PUMP_EMA_DETACH_PCT_MAX
    print(f"\nПампы ({mn:.0f}-{mx:.0f}% выше EMA20 1h). Найдено: {len(pumped)}\n")
    for p in pumped:
        print(f"  {p['symbol']}: close={p['close']:.4f} | EMA={p['ema20']:.4f} | +{p['detach_pct']}%")


if __name__ == "__main__":
    main()
