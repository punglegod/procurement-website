import re
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import COMMODITY_MAP, SOURCES, read_sci99_settings, sci99_configured
from ..models import PricePoint
from .common import AccessStopped, HttpSource, SourceError, checked_date, normalized, now, validate_url, visible_text


ACCOUNT_URL = "https://ucenter.sci99.com/usercenter/newaccount/getUserInfoDetail"
STATE_MESSAGES = {
    "pending_credentials": "凭据未配置",
    "password_js_required": "官网需 JS，账密模式不可用；请人工登录后配置 Cookie",
    "password_valid": "账密登录已确认，未抓取报价",
    "cookie_valid": "Cookie 有效，已由官网用户信息接口确认；未抓取报价",
    "cookie_invalid": "Cookie 已失效，官网明确返回未登录或会话失效",
    "cookie_unconfirmed": "Cookie 未确认，响应结构、跳转或网络状态无法确认；已停止",
    "login_unconfirmed": "账密登录结果未确认，已停止；请人工检查官网",
    "access_blocked": "触发验证码或风控已停手，请人工检查官网",
    "quote_pending": "尚无完整报价，等待09:00轮全量采集（登录有效不代表报价完整）",
}


class Sci99Stopped(AccessStopped):
    """分类只允许固定状态和文案；绝不携带上游响应或账号。"""
    def __init__(self, status):
        self.status = status
        super().__init__(STATE_MESSAGES[status])


def _risk_response(response):
    if response.status_code == 429:
        return True
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        # 只查错误提示，不把用户资料中任意字符串误作验证码要求。
        text = " ".join(str(payload.get(key, "")) for key in ("message", "msg", "error"))
        flagged = any(payload.get(key) is True for key in ("needCaptcha", "captchaRequired", "riskControl"))
        return flagged or bool(re.search(r"验证码|风控|访问频繁|访问过于频繁|安全验证|安全检查|人机验证|滑动验证|滑块|captcha|verify you are human", text, re.I))
    # 网关可能把 HTML 挑战页误标为 JSON；不依赖 Content-Type 识别安全拦截。
    tree = HTMLParser(response.text)
    text = visible_text(tree)
    return bool("HW_CHECK" in response.text
                or tree.css_first('input[name*="captcha"], input[id*="captcha"], .geetest_panel')
                or re.search(r"访问过于频繁|访问频繁|异常访问|安全验证|安全检查|滑动验证|人机验证|账号异常|账户异常|风控|请输入验证码|请完成验证|验证码错误|滑块验证|verify you are human", text, re.I))


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


class Sci99Adapter(HttpSource):
    def __init__(self, settings=None, **kwargs):
        super().__init__(interval=5.0, **kwargs)
        self.settings = settings if settings is not None else read_sci99_settings()
        self.auth_status = None

    def check_response(self, response, *, sci99=False):
        if _risk_response(response):
            raise Sci99Stopped("access_blocked")
        if str(response.request.url) == ACCOUNT_URL:
            self.check_cookie_response(response)
            return
        try:
            super().check_response(response, sci99=sci99)
        except AccessStopped as error:
            # common 只生成固定消息；不向外传播任何上游错误内容。
            risk = any(term in str(error) for term in ("验证码", "风控", "安全验证"))
            raise Sci99Stopped("access_blocked" if risk else "cookie_unconfirmed" if self.settings.get("SCI99_COOKIE") else "login_unconfirmed") from None

    @staticmethod
    def check_cookie_response(response):
        if _risk_response(response):
            raise Sci99Stopped("access_blocked")
        if 300 <= response.status_code < 400:
            raise Sci99Stopped("cookie_unconfirmed")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            try:
                from .common import check_access
                check_access(response, sci99=True)
            except AccessStopped as error:
                if any(term in str(error) for term in ("验证码", "风控", "安全验证")):
                    raise Sci99Stopped("access_blocked") from None
            except SourceError:
                pass
            raise Sci99Stopped("cookie_unconfirmed")
        try:
            payload = response.json(object_pairs_hook=_unique_json_pairs)
        except ValueError:
            raise Sci99Stopped("cookie_unconfirmed") from None
        if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
            raise Sci99Stopped("cookie_unconfirmed")
        code = payload.get("code")
        message = payload.get("message")
        # 4001 为真实匿名响应；错误文案仅作明确失效判定，不输出其原文。
        expired = isinstance(message, str) and bool(re.search(r"^(?:用户)?未登录[！!。]?\s*$|请(?:先|重新)登录|登录(?:已)?(?:过期|失效)|会话(?:已)?(?:过期|失效)", message))
        if response.status_code in (200, 401) and (type(code) is int and code == 4001 or expired):
            raise Sci99Stopped("cookie_invalid")
        user = payload.get("data")
        # 有效结构来自实测 GET：code=200，data 为当前用户，含正整数 user_info_id 和 user_name。
        if (response.status_code == 200 and type(code) is int and code == 200
                and isinstance(user, dict) and type(user.get("user_info_id")) is int and user["user_info_id"] > 0
                and isinstance(user.get("user_name"), str) and user["user_name"].strip() and not expired):
            return "cookie_valid"
        raise Sci99Stopped("cookie_unconfirmed")

    def authenticate(self, client):
        if self.settings.get("SCI99_COOKIE"):
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
            self.auth_status = "password_valid"

    def test_connection(self):
        if not sci99_configured(self.settings):
            raise SourceError("未配置")
        with nullcontext(self.client) if self.client else self.open_client() as client:
            self.authenticate(client)
            if self.settings.get("SCI99_COOKIE"):
                self.verify_cookie(client)
                return "cookie_valid"
            return "password_valid"

    def verify_cookie(self, client):
        try:
            self.request(client, "GET", ACCOUNT_URL, sci99=True, headers={"Accept": "application/json"})
            self.auth_status = "cookie_valid"
        except Sci99Stopped:
            raise
        except SourceError:
            raise Sci99Stopped("cookie_unconfirmed") from None

    def fetch(self):
        if not sci99_configured(self.settings):
            return []
        with nullcontext(self.client) if self.client else self.open_client() as client:
            self.authenticate(client)
            if self.settings.get("SCI99_COOKIE"):
                self.verify_cookie(client)
            from .sci99_quotes import fetch_all
            return fetch_all(self, client)

    def login(self, client):
        # 仅支持官网直接提供的普通表单；不模拟 JS 加密、不处理任何验证码。
        url = validate_url(self.settings.get("SCI99_LOGIN_URL") or "https://ucenter.sci99.com/", ("sci99.com",))
        response = self.request(client, "GET", url, sci99=True)
        tree = HTMLParser(response.text)
        forms = [form for form in tree.css("form") if form.css_first('input[type="password"][name]')]
        if len(forms) != 1:
            raise Sci99Stopped("password_js_required")
        form = forms[0]
        if form.attributes.get("method", "get").lower() != "post":
            raise AccessStopped("未识别到安全的官网 POST 登录表单，已停止；请改用 SCI99_COOKIE")
        inputs = form.css("input[name]")
        if any(re.search(r"captcha|verify|验证码", str(node.attributes), re.I) for node in inputs):
            raise Sci99Stopped("access_blocked")
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
            raise Sci99Stopped("quote_pending")
        latest_date = max(day for day, _ in matches)
        latest = [price for day, price in matches if day == latest_date]
        if len(latest) != 1:
            raise SourceError("ABS 同日报价不唯一，拒绝混用口径")
        # 旧框架口径独立标注，绝不冒充当前华南免费来源口径。
        return PricePoint(code="abs", price=latest[0], unit="元/吨", quote_basis="华东市场 ABS 0215A 现货均价",
                          quote_date=latest_date, source="sci99", source_name=SOURCES["sci99"]["name"],
                          source_url=source_url, fetched_at=now().replace(tzinfo=None))
