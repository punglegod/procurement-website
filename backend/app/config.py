from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
TIMEZONE = ZoneInfo("Asia/Shanghai")
# 固定北京时间四轮；09:00 完成后日报，15:30 原轮次继续保留。
FETCH_SLOTS = ((8, 0), (8, 30), (9, 0), (15, 30))
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
    CommodityConfig("abs", "ABS", "free_abs", "普拉司华南市场 ABS 中石油吉化（揭阳）0215A 现货参考价", enabled=True),
    CommodityConfig("pc", "PC", "free_pc", "普拉司华南市场 PC 山东利华益维远 WY-111BR 现货参考价", enabled=True),
    CommodityConfig("pmma", "PMMA", "free_pmma", "塑价365转载生意社橡塑大宗榜 PMMA 现货基准价（市场均价，非牌号成交价）", enabled=True),
    CommodityConfig("lithium_carbonate", "碳酸锂", "free_lithium_carbonate", "我的钢铁新能源 电池级碳酸锂（早盘）Li2CO3≥99.5% 现货中间价", enabled=True),
    CommodityConfig("dmc", "有机硅中间体（DMC）", "free_dmc", "有机硅商城 DMC 新料现货主流报价区间低端（非均价，税运未注明）", enabled=True),
    CommodityConfig("silicone_rubber", "硅橡胶", "free_silicone_rubber", "有机硅商城 普通生胶现货主流报价区间低端（非混炼胶，税运未注明）", enabled=True),
    CommodityConfig("pe", "PE", "free_pe", "普拉司华南市场 LLDPE 中石化海南 DFDA 7042 现货参考价", enabled=True),
    CommodityConfig("pp", "PP 粒", "free_pp", "普拉司华南市场 PP 福建中景石化 T30S 拉丝粒料现货参考价", enabled=True),
    CommodityConfig("bopet", "BOPET", "free_bopet", "我的钢铁/隆众公开摘要 江浙 BOPET 12μ印刷基膜现货商谈区间低端", enabled=True),
    CommodityConfig("paper", "包装用纸", "free_paper", "生意社交易平台 上海兆美纸业 140g/m² 瓦楞原纸 上海交货现货报价", enabled=True),
)
COMMODITY_MAP = {item.code: item for item in COMMODITIES}
SOURCES = {
    "free": {"name": "世铝网（南海灵通公开报价）", "url": "https://market.cnal.com/nanhai/"},
    "sci99": {"name": "卓创资讯", "url": "https://ochem.sci99.com/product/abs", "enabled": False},
    "free_abs": {"name": "普拉司网", "url": "https://www.plasway.com/news/market-flash-news?id=198",
                 "adapter": "abs:ABSAdapter", "product": "ABS", "grade": "0215A/中石油吉化（揭阳）"},
    "free_pc": {"name": "普拉司网", "url": "https://www.plasway.com/news/market-flash-news?id=198",
                "adapter": "pc:PCAdapter", "product": "PC", "grade": "WY-111BR/山东利华益维远"},
    "free_pmma": {"name": "塑价365（原始来源：生意社）", "url": "https://www.pla365.com/pmma/", "adapter": "pmma:PMMAAdapter"},
    "free_lithium_carbonate": {"name": "我的钢铁新能源", "url": "https://xny.mysteel.com/", "adapter": "lithium_carbonate:LithiumCarbonateAdapter"},
    "free_dmc": {"name": "有机硅商城", "url": "https://www.yjgmall.com/news/list.php?catid=73106", "adapter": "dmc:DMCAdapter", "label": "DMC"},
    "free_silicone_rubber": {"name": "有机硅商城", "url": "https://www.yjgmall.com/news/list.php?catid=73106", "adapter": "silicone_rubber:SiliconeRubberAdapter", "label": "普通生胶"},
    "free_pe": {"name": "普拉司网", "url": "https://www.plasway.com/news/market-flash-news?id=198", "adapter": "pe:PEAdapter",
                "product": "PE", "grade": "LLDPE/DFDA 7042/中石化海南", "header": "品名/型号/制造商"},
    "free_pp": {"name": "普拉司网", "url": "https://www.plasway.com/news/market-flash-news?id=198", "adapter": "pp:PPAdapter",
                "product": "PP", "grade": "T30S/福建中景石化"},
    "free_bopet": {"name": "我的钢铁（隆众公开摘要）", "url": "https://m.mysteel.com/hot/1647652.html", "adapter": "bopet:BOPETAdapter"},
    "free_paper": {"name": "生意社交易平台", "url": "https://www.rawmex.cn/index.php", "adapter": "paper:PaperAdapter"},
}


def read_sci99_settings() -> dict[str, str]:
    # 不读取进程环境中的账号，避免意外使用其他项目的凭据。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    return {key: (values.get(key) or "").strip() for key in (
        "SCI99_USER", "SCI99_PASS", "SCI99_COOKIE", "SCI99_ABS_URL", "SCI99_LOGIN_URL"
    )}


def sci99_configured(settings: dict[str, str]) -> bool:
    return bool(settings["SCI99_COOKIE"] or (settings["SCI99_USER"] and settings["SCI99_PASS"]))


def read_telegram_settings() -> dict[str, str]:
    # 每次读取根目录文件，配置修改后无需重启；不继承进程中的同名凭据。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    return {key: (values.get(key) or "").strip() for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}


def read_account_settings() -> dict[str, str]:
    # 凭据仅来自项目根目录文件；测试通过替换 ROOT 隔离正式配置。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    return {key: values.get(key) or "" for key in (
        "ADMIN_USERNAME", "ADMIN_PASSWORD", "RESEND_API_KEY", "EMAIL_FROM", "SESSION_COOKIE_SECURE"
    )} | {"EMAIL_FROM": values.get("EMAIL_FROM") or "sourcing@bbbhl.com",
          "SESSION_COOKIE_SECURE": (values.get("SESSION_COOKIE_SECURE") or "true").strip()}


def read_public_origin() -> str | None:
    # 公开来源不是凭据，可由进程显式覆盖；其他凭据仍只读取根配置文件。
    value = os.environ.get("PUBLIC_BASE_URL")
    if value is None:
        value = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False).get("PUBLIC_BASE_URL")
    if not value or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or any(character.isspace() for character in value) or "\\" in value):
            raise ValueError
        host = parsed.hostname.lower().encode("idna").decode("ascii")
        host = f"[{host}]" if ":" in host else host
        port = parsed.port
        suffix = f":{port}" if port is not None and port != {"http": 80, "https": 443}[parsed.scheme] else ""
        return f"{parsed.scheme}://{host}{suffix}"
    except (ValueError, UnicodeError):
        raise ValueError("PUBLIC_BASE_URL 必须是仅含 http/https 来源的地址，不含路径、凭据、查询或片段") from None


def read_settings_encryption_key() -> str:
    # 不生成、不回写；缺失时禁用后台凭据保存，其他功能仍可启动。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    return (values.get("SETTINGS_ENCRYPTION_KEY") or "").strip()


def latest_workday(day):
    # 本站工作日口径为周一至周五，不接入或猜测法定节假日日历。
    from datetime import timedelta
    return day - timedelta(days=max(0, day.weekday() - 4))
