"""品类路由与自动轮日完成标记；不改变报价条目或个人口径模型。"""
from sqlalchemy import select

from .config import COMMODITIES, COMMODITY_MAP
from .models import SiteSetting

ALL_CODES = tuple(item.code for item in COMMODITIES)
SCI99_CODES = tuple(code for code in ALL_CODES if code != "alu_a00")
AUTO_PRICE_PREFIX = "auto_price_day:"


def selected_codes(codes=None):
    if codes is None:
        return list(ALL_CODES)
    if not codes or len(codes) > len(ALL_CODES) or len(set(codes)) != len(codes):
        raise ValueError("请选择1～11个不重复的品类")
    if any(code not in COMMODITY_MAP for code in codes):
        raise ValueError("未知品类 code")
    return [code for code in ALL_CODES if code in codes]


def captured_codes(session, day):
    return {row.key.removeprefix(AUTO_PRICE_PREFIX) for row in session.scalars(
        select(SiteSetting).where(SiteSetting.key.in_([AUTO_PRICE_PREFIX + code for code in ALL_CODES]),
                                  SiteSetting.value == day.isoformat()))}


def record_captured(session, rows, day):
    if day is None:
        return
    for code in {row.code for row in rows if row.quote_date == day and row.price is not None
                 and row.price > 0 and row.unit and row.unit != "待核定"}:
        # 和价格写入共用事务；手动/缺报/昨日报价/单位待核均不能冒充今日自动完成。
        session.merge(SiteSetting(key=AUTO_PRICE_PREFIX + code, value=day.isoformat()))
