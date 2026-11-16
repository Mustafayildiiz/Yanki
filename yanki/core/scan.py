"""Sayfa tarama akışı (TAR-01..05, KUR-01..03, NFR-08, NFR-09).

Akış:

1. Hedef ayrıştırılır; ağ hedeflerinde ad çözülüp politika denetlenir.
2. Denetleyici proxy başlatılır; Chromium tüm trafiğini ondan geçirir.
3. Kalıcı olmayan, yalıtılmış bir tarayıcı bağlamında sayfa açılır.
4. ``load`` beklenir, ardından ağın durulması sınırlı süre beklenir (TAR-02).
5. axe-core enjekte edilir, öğeler toplanır (TAR-03), kurallar çalıştırılır.

Çekirdek async'tir. Faz 1'deki klavye modülü ve Faz 4'teki FastAPI aynı
olay döngüsü modelini kullanır; ``scan()`` eşzamanlı çağıranlar içindir.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime
from importlib.metadata import version as dist_version
from typing import TypeVar
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Response, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from yanki import __version__
from yanki.config import YankiConfig
from yanki.core.collect import collect_elements
from yanki.core.errors import ScanError, TargetError
from yanki.core.netguard import GuardProxy, NetworkPolicy
from yanki.core.rules import inject_axe, run_axe
from yanki.core.schema import (
    BlockedRequest,
    PageInfo,
    ScanReport,
    ToolVersions,
    finalize_findings,
)
from yanki.core.target import Target, parse_target

_CHROMIUM_ARGS = [
    # WebRTC'nin proxy dışı UDP trafiğini kapatır (NFR-08).
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
]

_NET_ERRORS = {
    "ERR_NAME_NOT_RESOLVED": ("dns_failed", "Sunucu adı çözülemedi."),
    "ERR_CONNECTION_REFUSED": ("connection_refused", "Sunucu bağlantıyı reddetti."),
    "ERR_CONNECTION_RESET": ("connection_failed", "Bağlantı sıfırlandı."),
    "ERR_CONNECTION_TIMED_OUT": ("connection_failed", "Bağlantı zaman aşımına uğradı."),
    "ERR_TUNNEL_CONNECTION_FAILED": ("connection_failed", "Sunucuya bağlanılamadı."),
    "ERR_CERT_": ("tls_error", "TLS sertifikası doğrulanamadı."),
    "ERR_SSL_": ("tls_error", "TLS bağlantısı kurulamadı."),
    "ERR_FILE_NOT_FOUND": ("file_not_found", "Dosya bulunamadı."),
    "ERR_UNSAFE_PORT": ("unsafe_port", "Chromium bu portu güvensiz sayıp engelliyor."),
    "ERR_ABORTED": ("navigation_failed", "Gezinme iptal edildi (ör. dosya indirme)."),
}


async def run_scan(
    target: str | Target,
    config: YankiConfig | None = None,
    *,
    policy: NetworkPolicy | None = None,
) -> ScanReport:
    """Bir hedefi tarar ve JSON'a hazır raporu döner.

    Raises:
        TargetError: hedef geçersiz ya da politika gereği reddedildi.
        ScanError: sayfa açılamadı ya da tarama tamamlanamadı (TAR-05).
    """
    config = config or YankiConfig()
    scan_cfg = config.scan
    started_at = datetime.now(UTC)
    tgt = target if isinstance(target, Target) else parse_target(target)
    policy = policy or NetworkPolicy(allow_private=scan_cfg.allow_private_network)

    if tgt.kind == "url" and tgt.host is not None and tgt.port is not None:
        decision = await policy.decide(tgt.host, tgt.port)
        if decision.reason == "private_address":
            raise TargetError(
                "blocked_target",
                f"{tgt.host} yerel ağ ya da özel bir adrese çözülüyor. "
                "Taramak için yerel ağ iznini açıkça verin (--allow-private-network).",
            )
        if decision.reason == "dns_failed":
            raise ScanError("dns_failed", f"Sunucu adı çözülemedi: {tgt.host}")

    proxy = GuardProxy(policy, connect_timeout_s=scan_cfg.navigation_timeout_ms / 1000)
    proxy_url = await proxy.start()
    warnings: list[str] = []
    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    headless=True,
                    chromium_sandbox=scan_cfg.chromium_sandbox,
                    proxy={"server": proxy_url},
                    args=_CHROMIUM_ARGS,
                )
            except PlaywrightError as exc:
                raise ScanError("browser_failed", f"Chromium başlatılamadı: {exc}") from exc
            try:
                # Her tarama yeni, kalıcı olmayan bir profilde çalışır (NFR-09).
                context = await browser.new_context(
                    viewport={
                        "width": scan_cfg.viewport_width,
                        "height": scan_cfg.viewport_height,
                    },
                    locale=scan_cfg.locale,
                    service_workers="block",
                    accept_downloads=False,
                )
                page = await context.new_page()
                response = await _navigate(page, tgt, config, proxy, warnings)

                frame_warnings = await _safe(inject_axe(page), "axe-core enjekte edilemedi")
                warnings.extend(frame_warnings)
                elements = await _safe(
                    collect_elements(page, max_chars=scan_cfg.context_max_chars),
                    "sayfa öğeleri toplanamadı",
                )
                axe_version, findings, rules_summary = await _safe(
                    run_axe(
                        page,
                        tags=config.rules.axe_tags,
                        severity_map=config.rules.severity_map,
                    ),
                    "axe-core çalıştırılamadı",
                )
                page_info = PageInfo(
                    requested=tgt.requested,
                    url=tgt.url,
                    final_url=page.url,
                    http_status=response.status if response else None,
                    title=await page.title(),
                    lang=await page.evaluate("document.documentElement.getAttribute('lang')"),
                )
                browser_version = browser.version
            finally:
                await browser.close()
    finally:
        await proxy.close()

    return ScanReport(
        tool=ToolVersions(
            yanki=__version__,
            axe_core=axe_version,
            playwright=dist_version("playwright"),
            browser=f"chromium {browser_version}",
        ),
        page=page_info,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        seed=config.seed,
        config=config.model_dump(mode="json"),
        elements=elements,
        findings=finalize_findings(findings),
        rules_summary=rules_summary,
        blocked_requests=[
            BlockedRequest(host=host, port=port, reason=reason)
            for host, port, reason in sorted({(b.host, b.port, b.reason) for b in proxy.blocked})
        ],
        warnings=warnings,
    )


def scan(target: str | Target, config: YankiConfig | None = None) -> ScanReport:
    """``run_scan`` için eşzamanlı sarmalayıcı."""
    return asyncio.run(run_scan(target, config))


async def _navigate(
    page: Page,
    tgt: Target,
    config: YankiConfig,
    proxy: GuardProxy,
    warnings: list[str],
) -> Response | None:
    scan_cfg = config.scan
    # Ana çerçevenin gezinme zinciri (yönlendirmeler dâhil). Politika
    # engellemesini proxy kaydıyla eşleştirmek için kullanılır.
    chain: list[str] = []

    def on_request(request: Request) -> None:
        if request.is_navigation_request() and request.frame == page.main_frame:
            chain.append(request.url)

    page.on("request", on_request)
    response: Response | None = None
    try:
        response = await page.goto(
            tgt.url, wait_until="load", timeout=scan_cfg.navigation_timeout_ms
        )
    except PlaywrightTimeoutError as exc:
        _raise_for_proxy_events(chain, proxy)
        raise ScanError(
            "navigation_timeout",
            f"Sayfa {scan_cfg.navigation_timeout_ms} ms içinde yüklenmedi: {tgt.url}",
        ) from exc
    except PlaywrightError as exc:
        _raise_for_proxy_events(chain, proxy)
        raise _navigation_error(exc, tgt.url) from exc
    finally:
        page.remove_listener("request", on_request)

    _raise_for_proxy_events(chain, proxy)
    if response is not None and response.status >= 400 and scan_cfg.fail_on_http_error:
        raise ScanError("http_error", f"Sunucu HTTP {response.status} döndü: {page.url}")

    if scan_cfg.network_idle_timeout_ms > 0:
        try:
            await page.wait_for_load_state("networkidle", timeout=scan_cfg.network_idle_timeout_ms)
        except PlaywrightTimeoutError:
            warnings.append(
                f"Ağ {scan_cfg.network_idle_timeout_ms} ms içinde durulmadı; "
                "sayfa o anki durumuyla tarandı (TAR-02)."
            )
    return response


_PROXY_FAILURES = {
    "dns_failed": ("dns_failed", "Sunucu adı çözülemedi"),
    "connection_refused": ("connection_refused", "Sunucu bağlantıyı reddetti"),
    "connection_timeout": ("connection_failed", "Sunucuya bağlanırken zaman aşımı"),
    "connection_failed": ("connection_failed", "Sunucuya bağlanılamadı"),
}


def _raise_for_proxy_events(chain: list[str], proxy: GuardProxy) -> None:
    """Ana çerçevenin gezinme zincirindeki bir adımı proxy reddettiyse ya da
    kuramadıysa, bunu Chromium'un hata metni yerine proxy kaydından raporlar."""
    hosts: list[tuple[str, int]] = []
    for url in chain:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https", "ws", "wss") and parts.hostname:
            default_port = 443 if parts.scheme in ("https", "wss") else 80
            hosts.append((parts.hostname, parts.port or default_port))
    for host, port in hosts:
        hit = proxy.was_blocked(host, port)
        if hit is not None:
            raise ScanError(
                "blocked_by_policy",
                f"Gezinme ağ politikası tarafından engellendi ({hit.host}:{hit.port}, "
                f"{hit.reason}). Yönlendirme yerel ağa ya da özel bir adrese gidiyor olabilir.",
            )
    for host, port in hosts:
        fail = proxy.failure_for(host, port)
        if fail is not None:
            code, message = _PROXY_FAILURES.get(fail.reason, _PROXY_FAILURES["connection_failed"])
            raise ScanError(code, f"{message}: {fail.host}:{fail.port}")


def _navigation_error(exc: PlaywrightError, url: str) -> ScanError:
    text = str(exc)
    for marker, (code, message) in _NET_ERRORS.items():
        if marker in text:
            return ScanError(code, f"{message} ({url})")
    first_line = text.splitlines()[0] if text else type(exc).__name__
    return ScanError("navigation_failed", f"Sayfa açılamadı: {first_line}")


_T = TypeVar("_T")


async def _safe(awaitable: Awaitable[_T], what: str) -> _T:
    """Modül adımını çalıştırır; tarayıcı hatasını ``ScanError``'a çevirir."""
    try:
        return await awaitable
    except PlaywrightError as exc:
        raise ScanError("analysis_failed", f"{what}: {exc}") from exc
