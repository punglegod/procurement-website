"""我的钢铁新能源公开现货表：固定电池级早盘口径，排除升贴水和晚盘。"""

from contextlib import nullcontext
from decimal import Decimal, InvalidOperation

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class LithiumCarbonateAdapter(HttpSource):
    code = "lithium_carbonate"

    def fetch(self) -> list[PricePoint]:
        url = SOURCES[COMMODITY_MAP[self.code].source]["url"]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", url)
            return [self.parse(response.text, url)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        validate_url(source_url, ("xny.mysteel.com",))
        tree = HTMLParser(html)
        table = tree.css_first("table#table-tsl")
        header = [normalized(c.text()) for c in table.css("thead th")] if table else []
        if header != ["名称", "品位", "最低价", "最高价", "中间价", "涨跌", "单位", "日期", "走势"]:
            raise SourceError("碳酸锂现货表头变化，拒绝读取升贴水表")
        rows = table.css('tbody tr[code="ID01551919"]')
        if len(rows) != 1:
            raise SourceError("未找到唯一的电池级碳酸锂早盘现货行")
        cells = [normalized(c.text()) for c in rows[0].css("td")]
        if len(cells) != 9 or cells[:2] != ["电池级碳酸锂（早盘）", "Li2CO3≥99.5%"] or cells[6] != "元/吨":
            raise SourceError("碳酸锂早盘等级、单位或行结构不符")
        try:
            low, high, price = map(Decimal, cells[2:5])
        except InvalidOperation:
            raise SourceError("公开现货价格不完整，可能需要登录；不猜测隐藏价格") from None
        if not all(p.is_finite() for p in (low, high, price)) or not 0 < low <= price <= high or (low + high) / 2 != price:
            raise SourceError("碳酸锂现货中间价与区间不一致")
        day = checked_date(cells[7])
        item = COMMODITY_MAP[self.code]
        return PricePoint(code=item.code, price=price, unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=day, source=item.source, source_name=SOURCES[item.source]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
