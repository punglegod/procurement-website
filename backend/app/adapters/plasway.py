"""普拉司公开日报：严格限定华南市场、制造商、牌号和当日报价列。"""

import re
from contextlib import nullcontext
from decimal import Decimal
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class PlaswayAdapter(HttpSource):
    code: str

    def fetch(self) -> list[PricePoint]:
        config = SOURCES[COMMODITY_MAP[self.code].source]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", config["url"])
            tree = HTMLParser(response.text)
            candidates = []
            for link in tree.css('a[href]'):
                match = re.search(re.escape(config["product"]) + r"市场参考价日报[（(](20\d{6})[）)]", link.text())
                if match:
                    stamp = match[1]
                    day = checked_date(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}")
                    url = validate_url(urljoin(config["url"], link.attributes["href"]), ("www.plasway.com",))
                    candidates.append((day, url))
            if not candidates:
                raise SourceError("未找到该品种公开日报，不切换品种或报价口径")
            _, url = max(candidates)
            response = self.request(client, "GET", url)
            return [self.parse(response.text, url)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        item = COMMODITY_MAP[self.code]
        config = SOURCES[item.source]
        validate_url(source_url, ("www.plasway.com",))
        tree = HTMLParser(html)
        title, body = tree.css_first("article h1"), tree.css_first("article .article-body")
        match = re.fullmatch(re.escape(config["product"]) + r"市场参考价日报[（(](20\d{6})[）)]", title.text().strip()) if title else None
        if not match or not body:
            raise SourceError("公开日报标题或正文结构变化")
        stamp = match[1]
        day = checked_date(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}")
        text = normalized(body.text())
        if (f"华南{config['product']}市场主流参考价区间" not in text
                or f"{config['product']}主流型号价格汇总表（元/吨）" not in text):
            raise SourceError("市场、品种或单位不匹配")
        matches = []
        for table in body.css("table"):
            rows = table.css("tr")
            header = [normalized(c.text()) for c in rows[0].css("th,td")] if rows else []
            if len(header) != 4 or header[0] != config.get("header", "型号/制造商") or header[2] != f"{day.month}月{day.day}日" or header[3] != "涨跌值":
                continue
            for row in rows[1:]:
                cells = [normalized(c.text()) for c in row.css("td,th")]
                if cells and cells[0] == normalized(config["grade"]):
                    if len(cells) != 4 or not re.fullmatch(r"\d+(?:\.\d+)?", cells[2]):
                        raise SourceError("指定牌号的当日报价无效")
                    matches.append(Decimal(cells[2]))
        if len(matches) != 1 or not matches[0].is_finite() or matches[0] <= 0:
            raise SourceError("未找到唯一的指定制造商/牌号报价")
        return PricePoint(code=item.code, price=matches[0], unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=day, source=item.source, source_name=config["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
