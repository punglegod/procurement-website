"""生意社交易平台公开商品报价：固定上海兆美 140g 瓦楞原纸。"""

from contextlib import nullcontext
from decimal import Decimal
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class PaperAdapter(HttpSource):
    code = "paper"

    def fetch(self) -> list[PricePoint]:
        url = SOURCES[COMMODITY_MAP[self.code].source]["url"]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", url, params={"_a": "ppi_news", "f": "list", "choice": "title", "terms": "瓦楞纸商品报价动态"})
            tree = HTMLParser(response.text)
            candidates = []
            for link in tree.css('a[href]'):
                match = re.search(r"瓦楞纸商品报价动态[（(](20\d{2}-\d{2}-\d{2})[）)]", link.text())
                if match:
                    target = validate_url(urljoin(url, link.attributes["href"]), ("www.rawmex.cn",))
                    candidates.append((checked_date(match[1]), target))
            if not candidates:
                raise SourceError("公开搜索页没有瓦楞纸报价文章")
            _, target = max(candidates)
            response = self.request(client, "GET", target)
            return [self.parse(response.text, target)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        validate_url(source_url, ("www.rawmex.cn",))
        tree = HTMLParser(html)
        title, body = tree.css_first(".new-detail h1"), tree.css_first(".new-detail-text")
        match = re.fullmatch(r"瓦楞纸商品报价动态[（(](20\d{2}-\d{2}-\d{2})[）)]", title.text().strip()) if title else None
        if not match or not body:
            raise SourceError("瓦楞纸公开报价文章结构变化")
        day = checked_date(match[1])
        stamp = tree.css_first(".new-detail > p")
        if not stamp or checked_date(stamp.text()) != day or "来源：生意社" not in normalized(stamp.text()):
            raise SourceError("瓦楞纸来源或标题/正文日期不一致")
        header = [normalized(n.text()) for n in body.css(".pn_nav span")]
        if header != ["交易商", "品牌/产地", "交货地", "最新报价"]:
            raise SourceError("瓦楞纸公开报价表头变化")
        prices = []
        for spec in body.css(".pn_name"):
            if not re.fullmatch(r"瓦楞原纸分类[:：]瓦楞纸;克重（g/m2）[:：]140(?:g)?;?", normalized(spec.text())):
                continue
            node = spec.next
            while node:
                classes = node.attributes.get("class", "").split()
                if "pn_name" in classes:
                    break
                if "pn_data" in classes:
                    cells = [normalized(n.text()) for n in node.css("span")]
                    if cells[:3] == ["上海兆美纸业有限公司", "兆美纸业", "上海市"]:
                        price = re.fullmatch(r"(\d+(?:\.\d+)?)元/吨", cells[3]) if len(cells) == 4 else None
                        if not price:
                            raise SourceError("指定 140g 原纸报价或单位无效")
                        prices.append(Decimal(price[1]))
                node = node.next
        if len(prices) != 1 or prices[0] <= 0:
            raise SourceError("未找到唯一的上海兆美 140g 瓦楞原纸报价，不改用其他克重或废纸")
        item = COMMODITY_MAP[self.code]
        return PricePoint(code=item.code, price=prices[0], unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=day, source=item.source, source_name=SOURCES[item.source]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
