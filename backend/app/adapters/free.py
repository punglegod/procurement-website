import re
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class FreeAdapter(HttpSource):
    index_url = SOURCES["free"]["url"]

    def fetch(self) -> list[PricePoint]:
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", self.index_url)
            tree = HTMLParser(response.text)
            candidates = []
            for link in tree.css("a[href]"):
                if re.fullmatch(r"\d{1,2}月\d{1,2}日南海有色（灵通）铝锭价格", normalized(link.text())):
                    url = urljoin(self.index_url, link.attributes["href"])
                    validate_url(url, ("market.cnal.com",))
                    match = re.search(r"/nanhai/(20\d{2})/(\d{2})-(\d{2})/", urlsplit(url).path)
                    if match:
                        candidates.append((tuple(map(int, match.groups())), url))
            if not candidates:
                raise SourceError("未找到南海灵通铝锭日度报价文章；页面结构可能变化")
            _, url = max(candidates)
            response = self.request(client, "GET", url)
            return [self.parse(response.text, url)]

    @staticmethod
    def parse(html: str, source_url: str) -> PricePoint:
        tree = HTMLParser(html)
        title = tree.css_first("h1.tit")
        content = tree.css_first(".marketcontent")
        stamp = tree.css_first(".content_nav .time")
        if not title or "南海有色（灵通）铝锭价格" not in title.text() or not content or not stamp:
            raise SourceError("免费源报价文章结构不匹配，拒绝猜测价格")
        quote_date = checked_date(stamp.text())
        if f"{quote_date.month}月{quote_date.day}日" not in title.text():
            raise SourceError("文章标题与报价日期不一致")
        if "单位：元/吨" not in normalized(content.text()) or "佛山地区（铝）" not in normalized(content.text()):
            raise SourceError("报价地区或单位不匹配")
        found = []
        for table in content.css("table"):
            rows = table.css("tr")
            if not any([normalized(c.text()) for c in row.css("td, th")] == ["品种", "型号", "价格区间", "均价", "涨跌"] for row in rows):
                continue
            for row in rows:
                cells = [normalized(cell.text()) for cell in row.css("td, th")]
                if "国产A00批售（送厂）" not in cells:
                    continue
                index = cells.index("国产A00批售（送厂）")
                if cells[:index] != ["佛山地区（铝）"] or len(cells) != index + 4:
                    raise SourceError("A00 行结构变化，拒绝混用口径")
                limits = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-—~]\s*(\d+(?:\.\d+)?)", cells[index + 1])
                try:
                    price = Decimal(cells[index + 2])
                    low, high = map(Decimal, limits.groups()) if limits else (Decimal(0), Decimal(0))
                except InvalidOperation:
                    raise SourceError("A00 均价不是有效数字") from None
                if not price.is_finite() or not 0 < low <= price <= high or (low + high) / 2 != price:
                    raise SourceError("A00 均价与价格区间不一致")
                found.append(price)
        if len(found) != 1:
            raise SourceError("未找到唯一的佛山国产 A00 送厂均价")
        item = COMMODITY_MAP["alu_a00"]
        return PricePoint(code=item.code, price=found[0], unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=quote_date, source="free", source_name=SOURCES["free"]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
