from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import urllib.parse
from typing import Any

import requests

from domain.tron_wallet import is_probable_tron_wallet
from services.aml.getblock_parser import (
    extract_amlcheckup_from_redirect_header,
    extract_checking_address,
    extract_csrf_from_html,
    find_hidden_csrf_field,
)
from services.http_policy import HttpRetryPolicy, HttpTimeoutPolicy

log = logging.getLogger(__name__)


class GetBlockAMLClient:
    BASE = "https://getblock.net"

    def __init__(
        self,
        *,
        identity: str,
        password: str,
        lang: str = "en",
        timeout_seconds: float = 30.0,
        retry_policy: HttpRetryPolicy | None = None,
    ):
        self.identity = identity
        self.password = password
        self.lang = lang
        self.timeout_policy = HttpTimeoutPolicy(
            connect_seconds=min(10.0, timeout_seconds),
            read_seconds=timeout_seconds,
            write_seconds=timeout_seconds,
            pool_seconds=min(5.0, timeout_seconds),
        )
        self.retry_policy = retry_policy or HttpRetryPolicy()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
                "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.BASE}{path}"

    def _get(self, path: str, **kwargs) -> requests.Response:
        resp = self.session.get(
            self._url(path),
            timeout=self.timeout_policy.as_requests(),
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def _post(self, path: str, **kwargs) -> requests.Response:
        return self.session.post(
            self._url(path),
            timeout=self.timeout_policy.as_requests(),
            **kwargs,
        )

    def close(self) -> None:
        self.session.close()

    def _csrf_cookie_value(self) -> str | None:
        for name, value in self.session.cookies.items():
            if name == "_csrf":
                return urllib.parse.unquote(value)
        return None

    def _extract_csrf_from_cookie(self) -> str | None:
        cookie_val = self._csrf_cookie_value()
        if not cookie_val:
            return None
        m = re.search(r's:\d+:"([^"]+)"', cookie_val)
        return m.group(1) if m else None

    def _best_csrf_token(self, html_text: str | None = None) -> str | None:
        if html_text:
            token = extract_csrf_from_html(html_text)
            if token:
                return token
        return self._extract_csrf_from_cookie() or self._csrf_cookie_value()

    def _get_with_retry(
        self,
        path: str,
        *,
        max_attempts: int = 20,
        delay_seconds: float = 3.0,
        **kwargs,
    ) -> requests.Response:
        policy = self.retry_policy.with_max_attempts(max_attempts)
        last_error: requests.RequestException | None = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self._get(path, **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if policy.should_retry(attempt=attempt):
                    self._wait_before_retry(policy, attempt, path=path)
                    continue
                raise
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if policy.should_retry(attempt=attempt, status_code=status):
                    retry_after = (
                        exc.response.headers.get("Retry-After")
                        if exc.response is not None
                        else None
                    )
                    self._wait_before_retry(
                        policy,
                        attempt,
                        path=path,
                        retry_after=retry_after,
                        minimum_delay_seconds=delay_seconds,
                    )
                    continue
                raise

        raise RuntimeError(f"Не удалось получить {path}: {last_error}")

    @staticmethod
    def _wait_before_retry(
        policy: HttpRetryPolicy,
        attempt: int,
        *,
        path: str,
        retry_after: str | None = None,
        minimum_delay_seconds: float = 0.0,
    ) -> None:
        delay = max(
            policy.delay_seconds(attempt=attempt, retry_after=retry_after),
            minimum_delay_seconds,
        )
        delay = min(delay, policy.max_delay_seconds)
        log.warning(
            "GetBlock request %s failed, retrying in %.1fs (%d/%d)",
            path,
            delay,
            attempt,
            policy.max_attempts,
        )
        time.sleep(delay)

    def login(self) -> None:
        login_url = f"/{self.lang}/user/sign-in/login"

        page = self._get(login_url)
        hidden_csrf = find_hidden_csrf_field(page.text)
        header_csrf = self._best_csrf_token(page.text)

        if not hidden_csrf:
            raise RuntimeError("Не удалось найти hidden _csrf на странице логина")
        if not header_csrf:
            raise RuntimeError("Не удалось найти x-csrf-token для логина")

        payload = {
            "_csrf": hidden_csrf,
            "LoginForm[identity]": self.identity,
            "LoginForm[password]": self.password,
            "LoginForm[rememberMe]": "1",
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(login_url),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": header_csrf,
        }

        resp = self._post(login_url, data=payload, headers=headers, allow_redirects=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"Ошибка логина: HTTP {resp.status_code}")

        check = self._get(f"/{self.lang}/riskscore", allow_redirects=True)
        if "/user/sign-in/login" in check.url.lower():
            raise RuntimeError("Логин не удался")

    def get_riskscore_page(self) -> requests.Response:
        resp = self._get(f"/{self.lang}/riskscore", allow_redirects=True)
        if "/user/sign-in/login" in resp.url.lower():
            raise RuntimeError("Сессия не авторизована: riskscore редиректит на логин")
        return resp

    def create_check(
        self,
        *,
        wallet: str,
        currency_code: str,
        token_id: str,
        user_id: str,
        aml_provider: str,
        direction: str,
        source: str,
        type_: str,
        checking_address: str | None = None,
        checking_tx_id: str = "",
    ) -> dict[str, Any]:
        page = self.get_riskscore_page()
        ajax_csrf = self._best_csrf_token(page.text)
        hidden_csrf = find_hidden_csrf_field(page.text) or ""

        if not ajax_csrf:
            raise RuntimeError("Не найден x-csrf-token для order-запроса")
        if not hidden_csrf:
            raise RuntimeError("Не найден hidden _csrf для order-запроса")
        if not user_id:
            raise RuntimeError("Не задан GETBLOCK_USER_ID")

        data = {
            "_csrf": hidden_csrf,
            "CheckingForm[checking_hash]": wallet,
            "CheckingForm[token_id]": token_id,
            "CheckingForm[type]": type_,
            "CheckingForm[user_id]": user_id,
            "CheckingForm[currency_code]": currency_code,
            "CheckingForm[direction]": direction,
            "CheckingForm[source]": source,
            "CheckingForm[checking_address]": checking_address or wallet,
            "CheckingForm[checking_tx_id]": checking_tx_id,
            "CheckingForm[aml_provider]": aml_provider,
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(f"/{self.lang}/riskscore"),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": ajax_csrf,
        }

        resp = self._post(
            f"/{self.lang}/explorer/order",
            data=data,
            headers=headers,
            allow_redirects=False,
        )

        result: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": resp.url,
            "headers": dict(resp.headers),
            "raw_text": resp.text[:3000],
        }

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            with contextlib.suppress(ValueError):
                result["json"] = resp.json()

        amlcheckup = extract_amlcheckup_from_redirect_header(result["headers"])
        if amlcheckup:
            result["amlcheckup"] = amlcheckup

        return result

    def get_transaction_check_form(
        self,
        *,
        tx_hash: str,
        currency_code: str,
        aml_provider: str,
        source: str,
    ) -> str:
        """Resolve the hidden address through GetBlock's hash-only quick-check form."""
        ajax_csrf = self.refresh_ajax_csrf()
        resp = self._post(
            f"/{self.lang}/site/ajax",
            data={
                "method": "getCheckForm",
                "checking_type": "1",
                "currency_code": currency_code,
                "checking_hash": tx_hash,
                "source": source,
                "hot_search": "true",
                "aml_provider": aml_provider,
            },
            headers={
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": self.BASE,
                "referer": self._url(f"/{self.lang}/riskscore"),
                "x-requested-with": "XMLHttpRequest",
                "x-csrf-token": ajax_csrf,
            },
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"GetBlock не смог открыть форму транзакции: HTTP {resp.status_code}")
        payload = resp.json()
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or not payload.get("success"):
            raise RuntimeError("GetBlock не смог подготовить проверку транзакции")
        address = extract_checking_address(str(payload.get("message") or ""))
        if not address or not is_probable_tron_wallet(address):
            raise RuntimeError("GetBlock не определил адрес для проверки по хэшу")
        return address

    def get_transaction_page(
        self, *, tx_hash: str, amlcheckup: str, currency_code: str
    ) -> dict[str, Any]:
        ajax_csrf = self.refresh_ajax_csrf()
        resp = self._post(
            f"/{self.lang}/site/ajax",
            data={"method": "getTxPage", "hash": tx_hash, "code": currency_code, "page": ""},
            headers={
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": self.BASE,
                "referer": self._url(
                    f"/{self.lang}/{currency_code}/tx/{tx_hash}?amlcheckup={amlcheckup}"
                ),
                "x-requested-with": "XMLHttpRequest",
                "x-csrf-token": ajax_csrf,
            },
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"GetBlock не смог открыть страницу транзакции: HTTP {resp.status_code}")
        return {"status_code": resp.status_code}

    def refresh_ajax_csrf(self) -> str:
        resp = self.get_riskscore_page()
        token = self._best_csrf_token(resp.text)
        if not token:
            raise RuntimeError("Не удалось получить CSRF token для AJAX")
        return token

    def get_address_page(
        self, *, wallet: str, amlcheckup: str, currency_code: str
    ) -> dict[str, Any]:
        ajax_csrf = self.refresh_ajax_csrf()

        data = {
            "method": "getAddressPage",
            "hash": wallet,
            "code": currency_code,
            "page": "",
            "urlParams[amlcheckup]": amlcheckup,
        }

        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.BASE,
            "referer": self._url(
                f"/{self.lang}/{currency_code}/address/{wallet}?amlcheckup={amlcheckup}"
            ),
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": ajax_csrf,
        }

        resp = self._post(
            f"/{self.lang}/site/ajax", data=data, headers=headers, allow_redirects=True
        )

        result: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": resp.url,
            "headers": dict(resp.headers),
            "raw_text": resp.text[:5000],
        }

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            with contextlib.suppress(ValueError):
                result["json"] = resp.json()

        return result

    def get_report_preview_html(
        self,
        *,
        amlcheckup: str,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
    ) -> str:
        resp = self._get_with_retry(
            f"/{self.lang}/report-preview/{amlcheckup}",
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
            allow_redirects=True,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": self._url(f"/{self.lang}/riskscore"),
                "upgrade-insecure-requests": "1",
            },
        )
        return resp.text

    def download_report(
        self,
        *,
        amlcheckup: str,
        output_path: str,
        max_attempts: int = 10,
        delay_seconds: float = 3.0,
    ) -> str:
        preview_url = f"/{self.lang}/report-preview/{amlcheckup}"
        download_url = f"/{self.lang}/report-download/{amlcheckup}"

        policy = self.retry_policy.with_max_attempts(max_attempts)
        last_error: requests.RequestException | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                self._get_with_retry(
                    preview_url,
                    max_attempts=1,
                    delay_seconds=delay_seconds,
                    allow_redirects=True,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "referer": self._url(f"/{self.lang}/riskscore"),
                        "upgrade-insecure-requests": "1",
                    },
                )

                with self._get(
                    download_url,
                    allow_redirects=True,
                    stream=True,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "referer": self._url(preview_url),
                        "upgrade-insecure-requests": "1",
                    },
                ) as resp:
                    with open(output_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)

                return output_path
            except requests.RequestException as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if isinstance(exc, requests.HTTPError) and exc.response is not None
                    else None
                )
                if policy.should_retry(attempt=attempt, status_code=status):
                    retry_after = (
                        exc.response.headers.get("Retry-After")
                        if exc.response is not None
                        else None
                    )
                    self._wait_before_retry(
                        policy,
                        attempt,
                        path=download_url,
                        retry_after=retry_after,
                        minimum_delay_seconds=delay_seconds,
                    )
                    continue
                raise

        raise RuntimeError(
            f"Не удалось скачать отчет после {policy.max_attempts} попыток: {last_error}"
        )
