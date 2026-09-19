"""
تحلیل آنچین - صادقانه: دیتای آنچین حرفه‌ای (Whale wallet tracking دقیق، Exchange net-flow کامل،
Dormant coins, Miner activity, Token unlock, Stablecoin flow, Supply distribution) در سطح
Glassnode/Nansen/Whale Alert **پولی** است و جایگزین رایگان کامل و دقیقی براش وجود نداره.

اینجا از mempool.space و blockchain explorer های عمومی (رایگان، بدون کلید، ولی Rate-Limit دارن)
یک تخمین محدود برای BTC می‌سازیم: تراکنش‌های بزرگ اخیر (به عنوان پروکسی Whale Activity).
برای بقیه‌ی موارد (Miner activity دقیق، Token unlock schedule، Stablecoin flow کامل) باید از
API پولی استفاده کرد؛ اینجا فیلد آن‌ها را با مقدار None و توضیح برمی‌گردانیم تا تو گزارش گم نشن.
"""
import logging
import requests

logger = logging.getLogger("onchain")

WHALE_TX_THRESHOLD_BTC = 100  # تراکنش‌های بالای این مقدار به عنوان "whale tx" در نظر گرفته میشن


def fetch_btc_large_transactions(limit_blocks: int = 1):
    """
    از mempool.space (رایگان، بدون کلید) آخرین چند بلاک رو می‌گیره و تراکنش‌های بزرگ رو پیدا می‌کنه.
    این فقط برای BTC کار می‌کنه (mempool.space مخصوص بیت‌کوینه).
    """
    try:
        tip = requests.get("https://mempool.space/api/blocks/tip/height", timeout=10).json()
        large_txs = []
        for h in range(tip, tip - limit_blocks, -1):
            block_hash = requests.get(f"https://mempool.space/api/block-height/{h}", timeout=10).text
            txs = requests.get(f"https://mempool.space/api/block/{block_hash}/txs", timeout=15).json()
            for tx in txs:
                total_out_sats = sum(o.get("value", 0) for o in tx.get("vout", []))
                total_out_btc = total_out_sats / 1e8
                if total_out_btc >= WHALE_TX_THRESHOLD_BTC:
                    large_txs.append({"txid": tx["txid"], "amount_btc": round(total_out_btc, 2)})
        return large_txs[:20]
    except Exception as e:
        logger.warning("btc large tx fetch failed: %s", e)
        return []


def onchain_summary(symbol: str) -> dict:
    base = symbol.split("/")[0].upper()
    result = {
        "whale_large_tx_sample": None,
        "exchange_inflow_outflow": None,
        "dormant_coins": None,
        "miner_activity": None,
        "token_unlock": None,
        "stablecoin_flow": None,
        "supply_distribution": None,
        "data_quality_note": (
            "برای BTC یک نمونه از تراکنش‌های بزرگ اخیر (whale proxy) از mempool.space رایگان گرفته میشه. "
            "بقیه‌ی معیارهای آنچین (Exchange Flow دقیق، Dormant Coins، Miner Activity، Token Unlock، "
            "Stablecoin Flow، Supply Distribution) نیاز به API پولی (Glassnode/Nansen/CryptoQuant) دارند "
            "و در نسخه‌ی رایگان قابل محاسبه‌ی دقیق نیستند - فیلدهای مربوطه None برمی‌گردند تا در گزارش گم نشوند."
        ),
    }
    if base == "BTC":
        result["whale_large_tx_sample"] = fetch_btc_large_transactions()
    return result
