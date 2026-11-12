import re
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES, read_sci99_settings, sci99_configured
from ..models import PricePoint
from .common import AccessStopped, HttpSource, SourceError, checked_date, normalized, now, validate_url


class Sci99Adapter(HttpSource):
    def __init__(self, settings=None, **kwargs):
        super().__init__(interval=5.0, **kwargs)
        self.settings = settings if settings is not None else read_sci99_settings()

    def fetch(self) -> list[PricePoint]:
        if not sci99_configured(self.settings):
            return []
        url = validate_url(self.settings.get("SCI99_ABS_URL") or SOURCES["sci99"]["url"], ("sci99.com",))
        if urlsplit(url).query or urlsplit(url).fragment:
            raise SourceError("ABS 页面请使用不含查询参数的固定 URL，避免将登录令牌写入数据库")
        with nullcontext(self.client) if self.client else self.open_client() as client:
            if self.settings["SCI99_COOKIE"]:
                cookies = SimpleCookie()
                try:
                    cookies.load(self.settings["SCI99_COOKIE"])
                except Exception:
                    raise SourceError("SCI99_COOKIE 格式不正确") from None
                if not cookies:
                    raise SourceError("SCI99_COOKIE 为空或无法解析")
                for key, value in cookies.items():
                    client.cookies.set(key, value.value, domain=".sci99.com", path="/")
            else:
                self.login(client)
            response = self.request(client, "GET", url, sci99=True)
            return [self.parse(response.text, url)]

    def login(self, client):
        # 仅支持官网直接提供的普通表单；不模拟 JS 加密、不处理任何验证码。
        url = validate_url(self.settings.get("SCI99_LOGIN_URL") or "https://ucenter.sci99.com/", ("sci99.com",))
        response = self.request(client, "GET", url, sci99=True)
        tree = HTMLParser(response.text)
        forms = [form for form in tree.css("form") if form.css_first('input[type="password"][name]')]
        if len(forms) != 1:
            raise AccessStopped("官网登录需 JS/人工验证，已停止；请人工登录后配置 SCI99_COOKIE")
        form = forms[0]
        if form.attributes.get("method", "get").lower() != "post":
            raise AccessStopped("未识别到安全的官网 POST 登录表单，已停止；请改用 SCI99_COOKIE")
        inputs = form.css("input[name]")
        if any(re.search(r"captcha|verify|验证码", str(node.attributes), re.I) for node in inputs):
            raise AccessStopped("登录表单要求验证码，已停止；请人工登录后配置 SCI99_COOKIE")
        password = form.css_first('input[type="password"][name]')
        users = [node for node in inputs if node.attributes.get("type", "text") in ("text", "email", "tel")]
        if len(users) != 1:
            raise AccessStopped("登录字段无法唯一识别，已停止；请改用 SCI99_COOKIE")
        fields = {node.attributes["name"]: node.attributes.get("value", "") for node in inputs if node.attributes.get("type") == "hidden"}
        fields[users[0].attributes["name"]] = self.settings["SCI99_USER"]
        fields[password.attributes["name"]] = self.settings["SCI99_PASS"]
        action = validate_url(urljoin(url, form.attributes.get("action", "")), ("sci99.com",))
        response = self.request(client, "POST", action, data=fields, sci99=True)
        text = HTMLParser(response.text).body
        if not text or not re.search(r"退出登录|安全退出|登录成功", text.text()):
            raise AccessStopped("登录结果未确认或登录异常，已停止；请人工检查账号并配置 SCI99_COOKIE")

    @staticmethod
    def parse(html: str, source_url: str) -> PricePoint:
        tree = HTMLParser(html)
        body = tree.body.text(separator=" ", strip=True) if tree.body else ""
        if re.search(r"请先登录|登录后查看|登录后可|无权查看|暂无权限|购买后查看|订阅后查看", body):
            raise AccessStopped("ABS 页面登录态失效或订阅权限不足，已停止")
        # 只接受表头和口径均明确的现货均价；不从资讯正文或价格区间猜测。
        matches = []
        required = ["品种", "市场", "牌号", "价格类型", "均价", "单位", "报价日期"]
        for table in tree.css("table"):
            header = None
            for row in table.css("tr"):
                cells = [normalized(node.text()) for node in row.css("th, td")]
                if all(field in cells for field in required):
                    if any(cells.count(field) != 1 for field in required):
                        raise SourceError("ABS 表头不唯一，拒绝猜测")
                    header = cells
                    continue
                if header is None or len(cells) != len(header):
                    continue
                values = dict(zip(header, cells))
                if [values[k] for k in required[:4]] != ["ABS", "华东", "0215A", "现货"]:
                    continue
                if values["单位"] != "元/吨":
                    raise SourceError("ABS 价格单位不匹配")
                try:
                    price = Decimal(values["均价"].replace(",", ""))
                except InvalidOperation:
                    raise SourceError("ABS 均价不可读，可能未登录或权限不足") from None
                if not price.is_finite() or price <= 0:
                    raise SourceError("ABS 均价无效")
                matches.append((checked_date(values["报价日期"]), price))
        if not matches:
            raise SourceError("ABS 未找到华东 0215A 现货均价表；页面可能动态加载，需核定已订阅报价页及解析规则")
        latest_date = max(day for day, _ in matches)
        latest = [price for day, price in matches if day == latest_date]
        if len(latest) != 1:
            raise SourceError("ABS 同日报价不唯一，拒绝混用口径")
        item = COMMODITY_MAP["abs"]
        return PricePoint(code="abs", price=latest[0], unit=item.unit, quote_basis=item.quote_basis,
                          quote_date=latest_date, source="sci99", source_name=SOURCES["sci99"]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
