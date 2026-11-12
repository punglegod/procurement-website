from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
TIMEZONE = ZoneInfo("Asia/Shanghai")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class CommodityConfig:
    code: str
    name: str
    source: str
    quote_basis: str
    unit: str = "元/吨"
    enabled: bool = False


COMMODITIES = (
    CommodityConfig("alu_a00", "南海灵通 A00 铝锭", "free", "南海灵通佛山国产A00批售（送厂）均价", enabled=True),
    CommodityConfig("abs", "ABS", "sci99", "华东市场 ABS 0215A 现货均价", enabled=True),
    CommodityConfig("pc", "PC", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("pmma", "PMMA", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("lithium_carbonate", "碳酸锂", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("dmc", "有机硅中间体（DMC）", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("silicone_rubber", "硅橡胶", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("pe", "PE", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("pp", "PP 粒", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("bopet", "BOPET", "sci99", "主流市场现货均价（具体规格待核定）"),
    CommodityConfig("paper", "包装用纸（瓦楞原纸）", "sci99", "瓦楞原纸主流市场现货均价（具体规格待核定）"),
)
COMMODITY_MAP = {item.code: item for item in COMMODITIES}
SOURCES = {
    "free": {"name": "世铝网（南海灵通公开报价）", "url": "https://market.cnal.com/nanhai/"},
    "sci99": {"name": "卓创资讯", "url": "https://ochem.sci99.com/product/abs"},
}


def read_sci99_settings() -> dict[str, str]:
    # 不读取进程环境中的账号，避免意外使用其他项目的凭据。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    return {key: (values.get(key) or "").strip() for key in (
        "SCI99_USER", "SCI99_PASS", "SCI99_COOKIE", "SCI99_ABS_URL", "SCI99_LOGIN_URL"
    )}


def sci99_configured(settings: dict[str, str]) -> bool:
    return bool(settings["SCI99_COOKIE"] or (settings["SCI99_USER"] and settings["SCI99_PASS"]))
