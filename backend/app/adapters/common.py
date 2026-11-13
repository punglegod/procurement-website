import re
import time
import threading
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import TIMEZONE, USER_AGENT


class SourceError(Exception):
    pass


class AccessStopped(SourceError):
    pass


def now() -> datetime:
    return datetime.now(TIMEZONE)


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("(", "（").replace(")", "）")


def validate_url(url: str, domains: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain) for domain in domains)):
        raise SourceError("来源 URL 必须是指定官网的 HTTPS 地址")
    return url


def visible_text(tree: HTMLParser) -> str:
    for node in tree.css("script, style, noscript"):
        node.decompose()
    return tree.body.text(separator=" ", strip=True) if tree.body else ""


def check_access(response: httpx.Response, sci99: bool = False):
    if response.status_code in (401, 403, 429):
        raise AccessStopped(f"HTTP {response.status_code}：登录异常或访问受限，已停止")
    if 300 <= response.status_code < 400:
        raise AccessStopped("来源发生跳转，可能需要登录，已停止")
    tree = HTMLParser(response.text)
    text = visible_text(tree)
    if re.search(r"访问过于频繁|访问频繁|异常访问|安全验证|安全检查|滑动验证|人机验证|账号异常|账户异常|风控|verify you are human", text, re.I) or "HW_CHECK" in response.text:
        raise AccessStopped("触发风控或安全验证，已停止；请人工检查官网")
    if sci99 and (tree.css_first('input[name*="captcha"], input[id*="captcha"], .geetest_panel')
                  or re.search(r"请输入验证码|请完成验证|验证码错误|滑块验证", text)):
        raise AccessStopped("触发验证码，已停止；请人工登录后配置 SCI99_COOKIE")
    if response.status_code >= 400:
        raise SourceError(f"来源返回 HTTP {response.status_code}")


class HttpSource:
    _host_requests = {}
    _pace_lock = threading.Lock()

    def __init__(self, client: httpx.Client | None = None, interval: float = 2.0):
        self.client = client
        self.interval = interval
        self.last_request = 0.0

    def check_response(self, response, *, sci99=False):
        check_access(response, sci99=sci99)

    def request(self, client: httpx.Client, method: str, url: str, *, sci99=False, **kwargs):
        # 每个请求之间留出间隔；仅网络错误或服务端错误允许重试一次。
        for attempt in range(2):
            # 多品种共用站点也遵守间隔，不因创建新 adapter 而连发请求。
            host = urlsplit(url).hostname
            with self._pace_lock:
                previous = max(self.last_request, self._host_requests.get(host, 0.0))
                time.sleep(max(0, self.interval - (time.monotonic() - previous)))
                self.last_request = time.monotonic()
                self._host_requests[host] = self.last_request
            try:
                response = client.request(method, url, timeout=15, follow_redirects=False, **kwargs)
            except httpx.TransportError:
                if attempt == 0 and method == "GET":
                    continue
                raise SourceError("网络连接失败或超时（15 秒）；请检查网络/代理") from None
            # 风控响应必须在重试逻辑之前检查，包括服务端错误页面。
            try:
                self.check_response(response, sci99=sci99)
            except AccessStopped:
                raise
            except SourceError:
                if response.status_code >= 500 and attempt == 0 and method == "GET":
                    continue
                raise
            return response
        raise SourceError("来源请求失败")

    def open_client(self):
        return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=False)


def checked_date(value: str) -> date:
    match = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})", value)
    if not match:
        raise SourceError("未找到完整报价日期，拒绝使用抓取日期代替")
    try:
        result = date(*map(int, match.groups()))
    except ValueError:
        raise SourceError("来源报价日期无效") from None
    if result > now().date():
        raise SourceError("来源报价日期在未来，拒绝入库")
    return result
