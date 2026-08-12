import logging
import threading
import time

import httpx
from curl_cffi.requests import Session as CurlSession
from curl_cffi.requests.exceptions import CurlError, RequestException

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class ScrapeBlockedError(RuntimeError):
    """OLX bloqueou a requisição (403 / Cloudflare)."""


class _CurlFetcher:
    """Fetcher real via curl_cffi com fingerprint de browser (impersonate).

    O Cloudflare bloqueia o JA3/HTTP2 fingerprint do ssl padrão do CPython
    (httpx) mesmo com User-Agent correto; o `impersonate` do curl_cffi emula o
    TLS de um Chrome real e passa na checagem.
    """

    def __init__(self, headers: dict, timeout: float, impersonate: str):
        self._session = CurlSession(
            headers=headers,
            timeout=timeout,
            impersonate=impersonate,
            allow_redirects=True,
        )

    def get(self, url: str):
        return self._session.get(url)


class _HttpxTransportFetcher:
    """Adapta um `httpx.BaseTransport` (usado nos testes offline) à interface
    do `OlxClient`. Devolve a própria `httpx.Response` (tem `.status_code`,
    `.text` e `.raise_for_status()`) — os handlers dos testes só olham a URL."""

    def __init__(self, transport: httpx.BaseTransport):
        self._transport = transport

    def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        return self._transport.handle_request(request)


# curl_cffi: erros de rede/heranças vêm de RequestException; alguns casos de
# baixo nível levantam CurlError direto (não é subclasse). Capturamos os dois.
NETWORK_ERRORS = (httpx.HTTPError, RequestException, CurlError)


class OlxClient:
    """Client com politeness: 1 requisição/segundo + retry com backoff.

    Transporte real = curl_cffi (impersonate); `transport=` injeta um
    `httpx.BaseTransport` (testes). Cada run cria sua própria instância, então
    o client é usado de forma single-threaded.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        delay: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        impersonate: str = "chrome",
    ):
        self._delay = delay
        self._lock = threading.Lock()
        self._last_request = 0.0
        if transport is not None:
            self._fetcher: _CurlFetcher | _HttpxTransportFetcher = _HttpxTransportFetcher(transport)
        else:
            self._fetcher = _CurlFetcher(
                {**BROWSER_HEADERS, "User-Agent": user_agent},
                timeout=timeout,
                impersonate=impersonate,
            )

    def get(self, url: str):
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait_turn()
            try:
                resp = self._fetcher.get(url)
            except NETWORK_ERRORS as e:
                if attempt == MAX_RETRIES:
                    raise
                log.warning("erro de rede (%s) na tentativa %s: %s", type(e).__name__, attempt, e)
                self._backoff(attempt)
                continue

            if resp.status_code == 403:
                raise ScrapeBlockedError(f"bloqueado (403) em {url}")
            if resp.status_code in (404, 410):
                # anúncio/página removidos — retorna para o chamador decidir
                return resp
            if resp.status_code in RETRY_STATUS:
                if attempt == MAX_RETRIES:
                    resp.raise_for_status()
                log.warning("status %s na tentativa %s de %s", resp.status_code, attempt, url)
                self._backoff(attempt)
                continue
            resp.raise_for_status()
            return resp

        raise RuntimeError("unreachable")

    def _wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._delay - (now - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2**attempt, 8))
