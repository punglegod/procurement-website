"""有机硅商城公开市场综述：读取主流报价区间低端及价格统计日。"""

from contextlib import nullcontext
from datetime import date
from decimal import Decimal
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES
from ..models import PricePoint
from .common import HttpSource, SourceError, checked_date, normalized, now, validate_url


class YjgmallAdapter(HttpSource):
    code: str

    def fetch(self) -> list[PricePoint]:
        url = SOURCES[COMMODITY_MAP[self.code].source]["url"]
        with nullcontext(self.client) if self.client else self.open_client() as client:
            response = self.request(client, "GET", url)
            tree = HTMLParser(response.text)
            candidates = []
            for node in tree.css("li"):
                link = node.css_first('a[href*="/news/show.php?itemid="]')
                stamp = re.search(r"20\d{2}-\d{2}-\d{2}\s*更新", node.text())
                if link and stamp:
                    target = validate_url(urljoin(url, link.attributes["href"]), ("www.yjgmall.com",))
                    candidates.append((checked_date(stamp[0]), target))
            if not candidates:
                raise SourceError("未找到有完整日期的公开市场综述")
            _, target = max(candidates)
            response = self.request(client, "GET", target)
            return [self.parse(response.text, target)]

    def parse(self, html: str, source_url: str) -> PricePoint:
        validate_url(source_url, ("www.yjgmall.com",))
        tree = HTMLParser(html)
        stamp, body = tree.css_first(".titlenews .other strong"), tree.css_first(".d-content #article")
        if not stamp or not body or not tree.css_first('a[href="https://www.yjgmall.com/news/list.php?catid=73106"]'):
            raise SourceError("市场综述结构或栏目变化")
        published = checked_date(stamp.text())
        headings = [n for n in body.css("strong") if normalized(n.text()) == "主流报价"]
        if len(headings) != 1 or headings[0].parent.tag != "div":
            raise SourceError("缺少唯一的主流报价区，拒绝从分析文字猜价格")
        parts = []
        node = headings[0].parent.next
        while node:
            parts.append(node.text())
            node = node.next
        section = normalized(" ".join(parts))
        dates = re.findall(r"价格统计时间[：:](\d{1,2})月(\d{1,2})日", section)
        if len(dates) != 1:
            raise SourceError("缺少明确价格统计日，不以发布日期代替")
        try:
            month, day = map(int, dates[0])
            quote_day = date(published.year, month, day)
            if quote_day > published:
                quote_day = date(published.year - 1, month, day)
        except ValueError:
            raise SourceError("价格统计日期无效") from None
        if not 0 <= (published - quote_day).days <= 31:
            raise SourceError("价格统计日与文章日期间隔异常")
        item = COMMODITY_MAP[self.code]
        label = SOURCES[item.source]["label"]
        matches = re.findall(r"(?:^|[；;])" + re.escape(label) + r"[：:](\d+(?:\.\d+)?)[-－—~](\d+(?:\.\d+)?)元/吨", section)
        if len(matches) != 1:
            raise SourceError("未找到唯一的指定产品主流报价区间")
        low, high = map(Decimal, matches[0])
        if not 0 < low <= high:
            raise SourceError("主流报价区间无效")
        # 固定保存原文公开的区间低端，口径中明确标注；不制造未发布的均价。
        return PricePoint(code=item.code, price=low, unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=quote_day, source=item.source, source_name=SOURCES[item.source]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
