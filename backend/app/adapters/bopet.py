"""我的钢铁公开行情摘要：只读江浙 BOPET 12μ 印刷基膜商谈区间低端。"""

from contextlib import nullcontext
from decimal import Decimal
import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class BOPETAdapter(HttpSource):
    code = "bopet"

    def fetch(self) -> list[PricePoint]:
        url = SOURCES[COMMODITY_MAP[self.code].source]["url"]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", url)
            return [self.parse(response.text, url)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        validate_url(source_url, ("m.mysteel.com",))
        tree = HTMLParser(html)
        quotes = []
        for node in tree.css("li.item-article"):
            title, body = node.css_first("a.list-item"), node.css_first("p.article-content")
            match = re.fullmatch(r"\[BOPET\][：:](20\d{2}年\d{1,2}月\d{1,2}日)江浙市场BOPET价格行情", normalized(title.text())) if title else None
            if not match or not body:
                continue
            day = checked_date(match[1])
            text = normalized(body.text())
            prices = re.findall(r"【市场动态】[：:]江浙市场BOPET12μ印刷基膜商谈参考(\d+)[-－—](\d+)元/吨", text)
            if len(prices) != 1:
                raise SourceError("江浙 BOPET 公开摘要缺少明确规格或价格区间；不读取隐藏报价")
            low, high = map(Decimal, prices[0])
            if not 0 < low <= high:
                raise SourceError("BOPET 商谈价格区间无效")
            quotes.append((day, low))
        if not quotes:
            raise SourceError("未找到江浙 BOPET 12μ 印刷基膜公开现货摘要")
        latest = max(d for d, _ in quotes)
        prices = {p for d, p in quotes if d == latest}
        if len(prices) != 1:
            raise SourceError("同日同口径公开摘要存在冲突")
        item = COMMODITY_MAP[self.code]
        return PricePoint(code=item.code, price=prices.pop(), unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=latest, source=item.source, source_name=SOURCES[item.source]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
