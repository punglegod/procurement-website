"""塑价365公开 PMMA 现货基准价；只读结构化 spot，不读期货或页面日期。"""

from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
import json

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, now, validate_url


class PMMAAdapter(HttpSource):
    code = "pmma"

    def fetch(self) -> list[PricePoint]:
        url = SOURCES[COMMODITY_MAP[self.code].source]["url"]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", url)
            return [self.parse(response.text, url)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        validate_url(source_url, ("www.pla365.com",))
        tree = HTMLParser(html)
        title = tree.css_first("main h1")
        nodes = tree.css('script.chd[type="application/json"]')
        if not title or not title.text().startswith("PMMA价格今日行情") or len(nodes) != 1:
            raise SourceError("PMMA 公开现货图表结构变化")
        try:
            data = json.loads(nodes[0].text(), parse_float=Decimal)
            if data["slug"] != "pmma" or data["name"] != "PMMA" or not data["spot"]:
                raise ValueError()
            rows = [(checked_date(str(d)), Decimal(str(p))) for d, p in data["spot"]]
            if len({d for d, _ in rows}) != len(rows) or any(not p.is_finite() or p <= 0 for _, p in rows):
                raise ValueError()
            day, price = max(rows)
        except (KeyError, ValueError, TypeError, InvalidOperation):
            raise SourceError("PMMA spot 日期或价格无效") from None
        notes = [n.text(separator=" ", strip=True) for n in tree.css(".chb .src")]
        if not any("现货：生意社橡塑大宗榜PMMA价格" in n and f"数据截至 {day.isoformat()}" in n for n in notes):
            raise SourceError("PMMA 现货说明或报价日期与 spot 不一致")
        if not tree.css_first('a[href="https://top.100ppi.com/zdb/detail-day---15.html"]'):
            raise SourceError("PMMA 原始来源标识变化")
        if "元/吨" not in tree.text():
            raise SourceError("PMMA 单位缺失")
        item = COMMODITY_MAP[self.code]
        return PricePoint(code=item.code, price=price, unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=day, source=item.source, source_name=SOURCES[item.source]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
