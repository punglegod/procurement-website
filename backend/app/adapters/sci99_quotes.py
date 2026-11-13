"""卓创商品页和分组采集；ID从各自官网页面核对，不复用ABS编号。"""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from selectolax.lexbor import LexborHTMLParser

from .common import SourceError, AccessStopped, validate_url, visible_text, checked_date, now


# 入口均取自官网公开商品导航；包装用纸包括四个独立商品页。
PRODUCT_PAGES = {
    "abs": ("https://ochem.sci99.com/product/abs",),
    "pc": ("https://ochem.sci99.com/product/pc",),
    "pmma": ("https://ochem.sci99.com/product/pmma",),
    "lithium_carbonate": ("https://ne.sci99.com/product/lithium_carbonate",),
    "dmc": ("https://nmt.sci99.com/product/silicone_intermediate",),
    "silicone_rubber": ("https://rubber.sci99.com/product/silicone_rubber",),
    "pe": ("https://plast.sci99.com/product/pe",),
    "pp": ("https://plast.sci99.com/product/pp_granules",),
    "bopet": ("https://plast.sci99.com/product/bopet",),
    "paper": ("https://paper.sci99.com/product/corrugated_paper",
              "https://paper.sci99.com/product/cardboard_paper",
              "https://paper.sci99.com/product/duplex_board",
              "https://paper.sci99.com/product/ivory_board"),
}


def page_context(html):
    def number(name):
        values = re.findall(r"\bvar\s+" + name + r"\s*=\s*(\d+)[ \t]*(?:;|\r?\n|$)", html)
        if len(values) != 1:
            raise SourceError("商品页编号未核定，未请求报价接口")
        return int(values[0])
    names = re.findall(r"\bvar\s+pageNavDictName\s*=\s*['\"]([^'\"<>]{1,100})['\"]\s*;", html)
    if len(names) != 1:
        raise SourceError("商品名称未核定，未请求报价接口")
    nav = number("navDictId")
    if nav != number("pageNavDictId"):
        raise SourceError("商品页编号不一致，拒绝猜测")
    return {"navDictId": nav, "navCategory": number("navCategory")}, names[0]


def page_html(response):
    # 部分行业站使用GB2312；优先响应头，再读HTML明确编码，避免中文身份键乱码。
    encoding = response.charset_encoding
    if not encoding:
        charset = re.search(br"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", response.content[:3000], re.I)
        encoding = charset[1].decode("ascii") if charset else "utf-8"
    if encoding.lower() in {"gb2312", "gbk"}:
        encoding = "gb18030"
    try:
        return response.content.decode(encoding)
    except (UnicodeError, LookupError):
        raise SourceError("商品页编码未确认，拒绝创建乱码条目") from None


def json_data(adapter, client, url, *, params=None, body=None):
    from .sci99 import Sci99Stopped, _unique_json_pairs
    response = adapter.request(client, "POST" if body is not None else "GET", url, sci99=True,
                               **({"json": body} if body is not None else {"params": params}))
    try:
        payload = response.json(object_pairs_hook=_unique_json_pairs)
    except ValueError:
        raise SourceError("报价接口未返回可核定的JSON") from None
    if isinstance(payload, dict) and type(payload.get("code")) is int and payload["code"] == 4001:
        raise Sci99Stopped("cookie_invalid")
    if (not isinstance(payload, dict) or type(payload.get("code")) is not int
            or payload["code"] != 200 or not isinstance(payload.get("data"), dict)):
        raise SourceError("报价接口未确认成功或订阅权限不足")
    return payload["data"]


def group_pages(adapter, client, origin, context, group, *, paged=False):
    # 当前商品样例分页字段均null；仅当上游明确声明分页，才按其pageSize/pageIndex续取。
    # 多商品前端另有已核定的pageIndex/pageSize=20契约，不与商品入口混用。
    prefix = "/visualPanelMultiProduct" if paged else "/visualPane"
    page, total, pages, size, seen, received = 1, None, None, 20 if paged else None, set(), 0
    while True:
        body = context | {"priceType": group, "filterItems": []}
        if paged:
            body.pop("navCategory", None)
        if size is not None:
            body |= {"pageIndex": page, "pageSize": size}
        data = json_data(adapter, client, origin + prefix + "/priceCenterDataV3", body=body)
        rows = data.get("tableDataArray")
        if not isinstance(data.get("tableHeadArray"), list) or not isinstance(rows, list):
            raise SourceError("报价表头或行结构未核定")
        if data.get("totalCount") is not None:
            if type(data["totalCount"]) is not int or data["totalCount"] < 0:
                raise SourceError("报价分页总数无效")
            if total is not None and total != data["totalCount"]:
                raise SourceError("报价分页总数变化，拒绝不完整结果")
            total = data["totalCount"]
        declared_pages, declared_size, declared_index = (data.get(k) for k in ("totalPage", "pageSize", "pageIndex"))
        if declared_pages is not None:
            if type(declared_pages) is not int or not 0 <= declared_pages <= 100:
                raise SourceError("报价分页页数无效或超过安全上限，未接受部分清单")
            if pages is not None and pages != declared_pages:
                raise SourceError("报价分页页数变化")
            pages = declared_pages
        if declared_index is not None and (type(declared_index) is not int or declared_index != page):
            raise SourceError("报价响应页码与请求不一致")
        if declared_size is not None:
            if type(declared_size) is not int or not 1 <= declared_size <= 1000 or size is not None and declared_size != size:
                raise SourceError("报价响应页大小无效或变化")
            size = declared_size
        needs_more = pages is not None and page < pages or total is not None and received + len(rows) < total
        if needs_more and (size is None or not paged and declared_index is None):
            raise SourceError("报价返回分页但页大小/页码缺失，未猜测参数或接受部分清单")
        import json
        fingerprint = json.dumps(rows, sort_keys=True, ensure_ascii=False)
        if fingerprint in seen or not rows and needs_more:
            raise SourceError("报价分页重复或提前结束，拒绝不完整结果")
        seen.add(fingerprint)
        received += len(rows)
        yield data
        if not needs_more:
            if total is not None and received != total or pages is not None and pages > 0 and page != pages:
                raise SourceError("报价分页条数或页数不一致")
            return
        if not rows or page >= 100:
            raise SourceError("分页提前结束或超过安全页数，未接受部分报价")
        page += 1


def fetch_all(adapter, client):
    result = []
    adapter.quote_results = []
    stopped = False
    for code, urls in PRODUCT_PAGES.items():
        if stopped:
            adapter.quote_results.append({"code": code, "status": "blocked", "message": "卓创已因登录/风控停手，本品种未请求"})
            continue
        try:
            points = []
            for url in urls:
                validate_url(url, ("sci99.com",))
                response = adapter.request(client, "GET", url, sci99=True)
                context, name = page_context(page_html(response))
                parsed = urlsplit(url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
                metadata = json_data(adapter, client, origin + "/visualPane/priceCenterPriceType", params=context)
                groups = metadata.get("groups")
                if not isinstance(groups, list) or not groups:
                    raise SourceError("品种价格分组为空，未接受空报价")
                names = [g.get("name") for g in groups if isinstance(g, dict)]
                if len(names) != len(groups) or any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
                    raise SourceError("品种价格分组结构不明确")
                for group in names:
                    group_rows = []
                    for data in group_pages(adapter, client, origin, context, group):
                        group_rows.extend(parse_table(data, code=code, product_name=name, group=group, url=url))
                    if not group_rows:
                        raise SourceError("价格分组为空，未接受不完整报价")
                    points.extend(group_rows)
            if not points:
                raise SourceError("品种报价为空")
            adapter.quote_results.append({"code": code, "status": "ok", "message": "全量分组已读取"})
            result.extend(points)
        except AccessStopped as error:
            stopped = True
            adapter.auth_status = getattr(error, "status", "access_blocked")
            adapter.quote_results.append({"code": code, "status": "blocked", "message": "卓创登录异常、验证码或风控，已停止后续卓创请求"})
        except SourceError:
            adapter.quote_results.append({"code": code, "status": "failed", "message": "全量报价结构、分页或网络未确认；保留最后数据"})
        except Exception:
            adapter.quote_results.append({"code": code, "status": "failed", "message": "本品种解析异常；其余品种继续，保留最后数据"})
    return result


def parse_table(data, *, code, product_name, group, url):
    from ..quotes import QuoteRow
    from .sci99 import Sci99Stopped
    headers, rows = data.get("tableHeadArray"), data.get("tableDataArray")
    if not isinstance(headers, list) or not headers or not isinstance(rows, list):
        raise SourceError("报价表格结构不明确")
    fields, price_fields = {}, []
    for header in headers:
        if not isinstance(header, dict) or not isinstance(header.get("field"), str) or not isinstance(header.get("title"), str):
            raise SourceError("报价表头结构不明确")
        field, title = header["field"], cell_text(header["title"])
        if field in fields:
            raise SourceError("报价表头字段重复")
        fields[field] = title
        # 样例Data的含义由完整日期表头确认，不把抓取日或文章日期冒充报价日。
        if re.fullmatch(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", title):
            price_fields.append((field, checked_date(title)))
    if not price_fields:
        raise SourceError("尚无核定的日期报价列，拒绝猜测Data字段")
    def by_title(row, titles):
        matched = [cell_text(row.get(field, "")) for field, title in fields.items() if title in titles]
        if len(matched) > 1:
            raise SourceError("条目身份列重复，拒绝混用")
        return matched[0] if matched else ""
    output=[]
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("报价行结构不明确")
        if row.get("loginStatus") is not None and (type(row["loginStatus"]) is not int or row["loginStatus"] != 1):
            raise Sci99Stopped("cookie_invalid")
        if row.get("powerType") is not None and (type(row["powerType"]) is not int or row["powerType"] != 1):
            raise SourceError("存在无订阅权限条目，未接受不完整清单")
        product = by_title(row, {"商品名称", "产品名称", "品种", "商品"}) or product_name
        market = by_title(row, {"市场名称", "市场", "地区"})
        specification = by_title(row, {"规格型号", "规格/牌号", "规格", "牌号", "型号"})
        factory = by_title(row, {"生产企业", "厂家"})
        # 厂家是牌号身份的一部分；同牌号不同厂家不能合并成一条历史。
        specification = " · ".join(v for v in (specification, factory) if v)
        change = by_title(row, {"涨跌", "涨跌额"})
        unit = by_title(row, {"单位"})
        tips = row.get("tips")
        if tips is not None and (not isinstance(tips, list) or not all(isinstance(t, str) for t in tips)):
            raise SourceError("报价说明结构不明确")
        units = [m.group(1).strip() for tip in tips or [] if (m := re.fullmatch(r"单位\s*[:：]\s*(.+)", cell_text(tip)))]
        if len(set([u for u in [unit, *units] if u])) > 1:
            raise SourceError("报价单位互相矛盾")
        overseas = re.search(r"香港|澳门|台湾|台灣|日本|韩国|韓國|东南亚|東南亞|东北亚|国际|國際|境外|海外|欧洲|歐洲|美国|美國|北美|印度|新加坡|越南|泰国|马来西亚|印尼|俄罗斯|中东|CFR|FOB|CIF", market + " " + group, re.I)
        unit = unit or (units[0] if units else "待核定" if overseas else "元/吨")
        for value, maximum in [(product,200),(specification,300),(market,200),(group,100),(unit,30),(change,100)]:
            if not isinstance(value,str) or len(value)>maximum:
                raise SourceError("报价字段超出长度限制")
        for field, day in price_fields:
            if field not in row:
                raise SourceError("报价行缺少已声明的日期列")
            raw = cell_text(row[field])
            low, high, price = price_value(raw)
            output.append(QuoteRow(code=code, product_name=product, specification=specification, market=market,
                price_type=group, price=price, low=low, high=high, price_text=raw, change_text=change,
                quote_date=day, unit=unit, source="sci99", source_name="卓创资讯", source_url=url,
                fetched_at=now().replace(tzinfo=None)))
    return output


def cell_text(value):
    if type(value) not in (str, int, float):
        raise SourceError("报价单元格类型未核定")
    if len(str(value)) > 5000:
        raise SourceError("报价单元格过长")
    tree = LexborHTMLParser("<body>" + str(value) + "</body>")
    visible_text(tree)
    return tree.body.text(separator="", strip=True).strip() if tree.body else ""


def price_value(raw):
    if raw in {"-", "--", "—", "暂无报价", ""}:
        return None,None,None
    numeric=r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,4})?"
    match=re.fullmatch(rf"\s*({numeric})(?:\s*[-~～–—至]\s*({numeric}))?\s*",raw)
    if not match:
        raise SourceError("价格为空、锁定或格式未核定，未接受部分清单")
    try:
        first=Decimal(match[1].replace(",",""));second=Decimal(match[2].replace(",","")) if match[2] else None
    except InvalidOperation:
        raise SourceError("报价数值无效") from None
    if not 0 < first < Decimal("10000000000000") or second is not None and (second < first or second >= Decimal("10000000000000")):
        raise SourceError("报价数值或区间无效")
    return (first,second,(first+second)/2) if second is not None else (None,None,first)
