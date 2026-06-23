"""金额格式化工具 — Decimal 精确舍入，消除 IEEE 754 浮点误差。"""
from decimal import Decimal, ROUND_HALF_UP

_TWO_PLACES = Decimal('0.01')


def fmt_money(value):
    """将任意数值精确舍入到两位小数（分），返回 float。

    Decimal 的 quantize 在十进制下做舍入，完全避开 IEEE 754 的二进制边界坑。
    round(2.675, 2) → 2.67 之类的问题在这里不存在。
    """
    return float(Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
