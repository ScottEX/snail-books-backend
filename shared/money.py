"""金额精确计算工具 — 全程 Decimal，消除 IEEE 754 浮点误差。

使用约定：
  1. SQL 读出的金额立即 to_decimal(value)
  2. 所有加减乘除在 Decimal 域完成
  3. 最终输出时 fmt_money(d) 转回 float

这样就彻底避开二进制浮点的累积误差。
"""
from decimal import Decimal, ROUND_HALF_UP

_TWO_PLACES = Decimal('0.01')


def to_decimal(value):
    """将任意数值转为 Decimal，保留精确表示。

    适用场景：从 SQLite SUM/amount 读出的 float 或 None。
    用法：d = to_decimal(row['amount'])
    """
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def fmt_money(value):
    """将 Decimal（或 float/int）精确舍入到两位小数（分），返回 float。

    Decimal 的 quantize 在十进制下做舍入，完全避开 IEEE 754 边界坑。
    round(2.675, 2) → 2.67 之类的问题在这里不存在。
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return float(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
