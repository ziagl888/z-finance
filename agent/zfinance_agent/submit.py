"""The z-finance receipts API: agent runs and receipt hand-over (``/api/receipts``, task -016).

A connection error, a timeout or a 5xx answer raises ``ApiUnavailable``: the run stops and the
message stays unread (AK6). A 4xx answer for a receipt raises ``ApiRefused``: the API will not take
this photo, so trying again next run would not help.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from zfinance_agent.extract import Extraction

RECEIPTS = "/api/receipts"


class ApiUnavailable(Exception):
    """The API cannot be reached or answered with a server error."""


class ApiRefused(Exception):
    """The API refused a request (4xx); ``detail`` is its message."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Submitted:
    """The API's answer for one receipt (``SubmitOut``)."""

    status: str
    receipt_id: int | None
    transaction_id: int | None
    reason: str | None
    duplicate_of: int | None
    matched: bool


def fields_for(extraction: Extraction | None, hints: str, message_ref: str, run_id: int | None) -> dict[str, str]:
    """Form fields of ``POST /api/receipts``; ``None`` extraction = unreadable (review item)."""
    fields: dict[str, str] = {"hints": hints, "message_ref": message_ref}
    if run_id is not None:
        fields["run_id"] = str(run_id)
    if extraction is None:
        fields.update(unreadable="true", confidence="0")
        return fields
    values: dict[str, Any] = {
        "date": extraction.date.isoformat() if extraction.date else None,
        "amount_cents": extraction.amount_cents,
        "merchant": extraction.merchant,
        "payment_method": extraction.payment_method,
        "category_suggestion": extraction.category_suggestion,
        "confidence": extraction.confidence,
        "currency": extraction.currency,
        "unreadable": "true" if extraction.unreadable else "false",
    }
    if extraction.line_items:
        values["line_items"] = json.dumps([item.model_dump() for item in extraction.line_items], ensure_ascii=False)
    fields.update({key: str(value) for key, value in values.items() if value is not None})
    return fields


class ReceiptsApi:
    """Thin client over an ``httpx.Client`` whose ``base_url`` is the z-finance server."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiUnavailable(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise ApiUnavailable(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", ""))[:200]
            except ValueError:
                detail = ""
            raise ApiRefused(response.status_code, detail)
        try:
            return response.json() if response.content else None
        except ValueError as exc:
            raise ApiUnavailable("answer is not JSON") from exc

    def start_run(self, note: str) -> int:
        """``POST /api/receipts/runs``; the id to pass with every receipt."""
        return int(self._request("POST", f"{RECEIPTS}/runs", json={"note": note})["id"])

    def finish_run(self, run_id: int, counts: dict[str, int], note: str | None) -> None:
        """``PUT /api/receipts/runs/{id}`` with the run's counts."""
        self._request("PUT", f"{RECEIPTS}/runs/{run_id}", json={"counts": counts, "note": note})

    def submit(self, photo: bytes, extension: str, fields: dict[str, str]) -> Submitted:
        """``POST /api/receipts``: one photo with its fields."""
        media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension]
        body = self._request("POST", RECEIPTS, data=fields, files={"photo": (f"receipt.{extension}", photo, media)})
        return Submitted(
            status=str(body["status"]),
            receipt_id=body.get("receipt_id"),
            transaction_id=body.get("transaction_id"),
            reason=body.get("reason"),
            duplicate_of=body.get("duplicate_of"),
            matched=bool(body.get("matched")),
        )
