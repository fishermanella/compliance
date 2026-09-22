"""HTTP / 浏览器抓取封装。

- 统一 UA、超时、错误处理；抓取失败一律抛出 FetchError，绝不返回伪造内容。
- Playwright 为可选依赖，缺失时明确降级而不是假装成功。
- 本模块不做任何验证码/登录绕过：检测到拦截即上报 blocked。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 常见拦截/验证页特征（命中即视为被拦截，不再尝试绕过）
BLOCK_MARKERS = (
    "环境异常",
    "去验证",
    "安全验证",
    "操作频繁",
    "访问过于频繁",
    "请在微信客户端打开",
    "请在微信客户端打开链接",
    "verify you are human",
    "checking your browser",
    "captcha",
    "cf-challenge",
    "attention required",
    "access denied",
    "登录后查看",
    "请先登录",
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ComplianceRadar/0.1 (+internal-monitoring)"
)


class FetchError(RuntimeError):
    """抓取失败（网络、状态码、依赖缺失）。"""


@dataclass
class FetchResponse:
    url: str
    status: int
    text: str
    final_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.final_url:
            self.final_url = self.url


def requests_available() -> bool:
    try:
        import requests  # noqa: F401
    except ImportError:
        return False
    return True


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def detect_block(text: Optional[str]) -> Optional[str]:
    """返回命中的拦截特征（用于说明原因），未命中返回 None。"""
    if not text:
        return None
    lowered = text[:20000].lower()
    for marker in BLOCK_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


class HttpClient:
    """基于 requests 的同步客户端。测试可注入 session。"""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20,
        session: Any = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._session = session

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise FetchError("requests 未安装，无法执行 HTTP 抓取") from exc
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        return self._session

    def get(self, url: str, *, timeout: Optional[float] = None) -> FetchResponse:
        session = self._ensure_session()
        try:
            response = session.get(url, timeout=timeout or self.timeout, allow_redirects=True)
        except Exception as exc:  # requests 异常体系较宽，统一转换
            raise FetchError(f"请求失败 {url}: {exc}") from exc

        status = int(getattr(response, "status_code", 0))
        text = getattr(response, "text", "") or ""
        if status >= 400:
            raise FetchError(f"HTTP {status} {url}")
        return FetchResponse(
            url=url,
            status=status,
            text=text,
            final_url=str(getattr(response, "url", url) or url),
            headers={k.lower(): v for k, v in dict(getattr(response, "headers", {}) or {}).items()},
        )


class PlaywrightFetcher:
    """无头浏览器抓取（DataGuidance 主引擎、微信兜底）。

    仅用于渲染与取回页面内容；不执行任何登录、验证码或风控绕过动作。
    """

    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT, timeout_ms: int = 45000) -> None:
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms

    @staticmethod
    def available() -> bool:
        return playwright_available()

    def fetch(self, url: str, *, wait_selector: Optional[str] = None) -> str:
        if not self.available():
            raise FetchError("playwright 未安装，无法使用浏览器抓取")
        from playwright.sync_api import sync_playwright  # 延迟导入

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=self.user_agent, locale="zh-CN")
                page = context.new_page()
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=self.timeout_ms)
                    except Exception:
                        pass  # 选择器未出现不致命，交给上层判断
                html = page.content()
                context.close()
                return html
            finally:
                browser.close()

    def fetch_rendered(self, url: str, *, wait_selector: Optional[str] = None) -> tuple[str, str]:
        """返回 (html, 渲染后纯文本)。"""
        if not self.available():
            raise FetchError("playwright 未安装，无法使用浏览器抓取")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=self.user_agent, locale="zh-CN")
                page = context.new_page()
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=self.timeout_ms)
                    except Exception:
                        pass
                html = page.content()
                try:
                    text = page.inner_text("body")
                except Exception:
                    text = ""
                context.close()
                return html, text
            finally:
                browser.close()

    def fetch_with_click(
        self,
        url: str,
        *,
        click_selector: Optional[str] = None,
        wait_selector: Optional[str] = None,
    ) -> str:
        """加载页面后点击指定元素（例如 DataGuidance 的 ALL 标签）再取回内容。

        点击失败不视为致命错误，交由上层根据结果判定覆盖率。
        """
        if not self.available():
            raise FetchError("playwright 未安装，无法使用浏览器抓取")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=self.user_agent, locale="zh-CN")
                page = context.new_page()
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                if click_selector:
                    try:
                        page.click(click_selector, timeout=self.timeout_ms)
                        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                    except Exception:
                        pass
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=self.timeout_ms)
                    except Exception:
                        pass
                html = page.content()
                context.close()
                return html
            finally:
                browser.close()


def parse_with_bs4(html: str, parser: str = "html.parser") -> Any:
    """解析 HTML；beautifulsoup4 缺失时返回 None（调用方需自行降级）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    return BeautifulSoup(html or "", parser)
