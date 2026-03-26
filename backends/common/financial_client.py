"""SOAP client wrapper for the financial transactions service."""

import threading
from typing import Literal

import requests
from zeep import Client, Settings
from zeep.exceptions import Fault as ZeepFault
from zeep.exceptions import TransportError
from zeep.transports import Transport


class FinancialClientError(Exception):
    """Base financial service client error."""


class FinancialUnavailableError(FinancialClientError):
    """Financial service unavailable or returned invalid payload."""


class FinancialInvalidCardError(FinancialClientError):
    """Financial service rejected card details as invalid."""


class FinancialClient:
    """Typed wrapper around SOAP financial transaction calls."""

    def __init__(self, wsdl_url: str):
        self.wsdl_url = wsdl_url
        self._lock = threading.Lock()
        self._client: Client | None = None

    def _ensure_client(self) -> Client:
        with self._lock:
            if self._client is not None:
                return self._client

            try:
                # Lazily build and cache the SOAP client; zeep initialization is expensive.
                transport = Transport(timeout=10, operation_timeout=10)
                settings = Settings(strict=False, xml_huge_tree=False)
                self._client = Client(
                    wsdl=self.wsdl_url,
                    transport=transport,
                    settings=settings,
                )
            except Exception as exc:
                raise FinancialUnavailableError(
                    "Financial transactions service is unavailable"
                ) from exc

            return self._client

    def process_transaction(
        self,
        *,
        user_name: str,
        credit_card_number: str,
        expiration_date: str,
        security_code: str,
    ) -> Literal["approved", "declined"]:
        client = self._ensure_client()
        try:
            result = client.service.ProcessTransaction(
                user_name=user_name,
                credit_card_number=credit_card_number,
                expiration_date=expiration_date,
                security_code=security_code,
            )
        except ZeepFault as exc:
            code = str(getattr(exc, "code", "") or "")
            if "InvalidCard" in code:
                raise FinancialInvalidCardError(str(exc)) from exc
            raise FinancialUnavailableError(
                "Financial transactions service failed"
            ) from exc
        except (TransportError, requests.RequestException, OSError) as exc:
            with self._lock:
                # Force re-initialization on the next call after transport-level failures.
                self._client = None
            raise FinancialUnavailableError(
                "Financial transactions service is unavailable"
            ) from exc
        except Exception as exc:
            raise FinancialUnavailableError(
                "Financial transactions service failed"
            ) from exc

        normalized = str(result).strip().lower()
        if normalized == "yes":
            return "approved"
        if normalized == "no":
            return "declined"

        raise FinancialUnavailableError("Financial transactions service returned invalid response")
