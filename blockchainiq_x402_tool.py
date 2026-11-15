"""LangChain tools for Blockchain IQ's x402 pay-per-call API.

Blockchain IQ (https://www.blockchainiq.co) sells market-data resources
through the x402 protocol: a GET returns HTTP 402 with a ``Payment-Required``
header; an x402 client signs an EIP-3009 authorization for the quoted amount
(USDC on Base) and retries with the payment header.

This module gives a LangChain agent two tools:

* ``list_blockchainiq_resources`` — free. Lists the paid catalogue
  (33 resources, $0.01-$2.00) plus the free sample.
* ``get_blockchainiq_resource`` — free to *quote*, paid to *fetch*.
  By default it stops at the 402 and returns the decoded payment
  requirements (network, amount, asset, payTo) so the agent can decide.
  With ``pay=True`` it executes the paid call — but only if
  ``X402_EVM_PRIVATE_KEY`` is set in the environment, only on Base
  (eip155:8453), only in USDC, only to the payTo address published in the
  live catalogue, and only up to ``max_amount_usd`` (default $0.01,
  hard-capped at $0.05). No key is ever logged or accepted as an argument.

Built for the Blockchain IQ x402 build bounty
(https://www.blockchainiq.co/blog/x402-build-bounties, closes 31 Dec 2026):
the cheapest provable paid call is ``brief`` at $0.01.

Safety rules (mirroring the bounty author's standing rules):
* Never sign a transaction, approve a token, or sign an unclear message.
  The x402 "exact" scheme only signs an EIP-712 ``transferWithAuthorization``
  for the exact quoted amount — no on-chain transaction is sent by the
  client; the facilitator settles it.
* The private key is read from the environment only, never from a tool
  argument, never printed, never written to disk.
* Amount, asset and payTo are cross-checked against the live catalogue's
  published payment rails before any signature is produced.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

# VM quirk: the egress proxy env ships NO_PROXY with "[::1]", which breaks
# httpx with "Invalid port: ':1]'". Sanitize before httpx is used.
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

import httpx
from langchain_core.tools import tool

BASE_URL = "https://www.blockchainiq.co"
CATALOG_URL = f"{BASE_URL}/api/x402/v1"
BASE_NETWORK = "eip155:8453"  # Base mainnet
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # verified on-chain 15 Nov 2026: name "USD Coin", symbol USDC, 6 decimals
USDC_DECIMALS = 6
DEFAULT_MAX_AMOUNT_USD = 0.01
HARD_MAX_AMOUNT_USD = 0.05
PAYMENT_REQUIRED_HEADER = "Payment-Required"


class PaymentRefused(Exception):
    """Raised when a paid call is blocked by a safety check."""


def _get(
    url: str, headers: dict[str, str] | None = None, timeout: float = 30
) -> httpx.Response:
    return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)


def _decode_payment_required(header_value: str) -> dict[str, Any]:
    """Decode the base64 Payment-Required header into a plain dict."""
    from x402 import parse_payment_required

    raw = base64.b64decode(header_value)
    req = parse_payment_required(raw)
    return req.model_dump(mode="json")


def list_resources() -> list[dict[str, Any]]:
    """Fetch the free catalogue and return one dict per resource."""
    resp = _get(CATALOG_URL)
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "id": r["id"],
            "method": r.get("method", "GET"),
            "path": r["path"],
            "price": r.get("price", "?"),
            "price_class": r.get("priceClass"),
            "title": r.get("title", ""),
            "description": r.get("description", ""),
        }
        for r in data.get("data", [])
    ]


def get_resource_url(resource_id: str) -> str:
    """Resolve a catalogue resource id to its full URL."""
    for r in list_resources():
        if r["id"] == resource_id:
            return BASE_URL + r["path"]
    raise ValueError(
        f"unknown resource {resource_id!r}; call list_blockchainiq_resources first"
    )


def quote_resource(resource_id: str) -> dict[str, Any]:
    """GET a resource without paying.

    Free resources return their data (``paid: false``). Paid resources return
    HTTP 402; the decoded payment requirements are returned instead
    (``paid: false``, ``payment_required`` with the accepts list).
    Nothing is signed and nothing is charged.
    """
    url = get_resource_url(resource_id)
    resp = _get(url)
    if resp.status_code == 200:
        return {"resource": resource_id, "paid": False, "data": resp.json()}
    if resp.status_code == 402:
        header = resp.headers.get(PAYMENT_REQUIRED_HEADER)
        if not header:
            raise RuntimeError("402 without a Payment-Required header")
        return {
            "resource": resource_id,
            "paid": False,
            "payment_required": _decode_payment_required(header),
        }
    resp.raise_for_status()
    raise RuntimeError(f"unexpected status {resp.status_code} for {resource_id}")


def _base_accept(accepts: list[dict[str, Any]]) -> dict[str, Any]:
    for a in accepts:
        if a.get("network") == BASE_NETWORK:
            return a
    raise PaymentRefused(f"no accept for {BASE_NETWORK} in payment requirements")


def _check_accept_against_catalog(accept: dict[str, Any]) -> float:
    """Validate the Base accept against the live catalogue rails.

    Returns the quoted price in USD. Raises PaymentRefused on any mismatch.
    """
    resp = _get(CATALOG_URL)
    resp.raise_for_status()
    rails = resp.json().get("paymentRails", [])
    base_rail = next(
        (r for r in rails if r.get("network") == BASE_NETWORK), None
    )
    if base_rail is None:
        raise PaymentRefused("catalogue has no Base payment rail")
    expected_pay_to = base_rail.get("payTo", "").lower()
    if accept.get("payTo", "").lower() != expected_pay_to:
        raise PaymentRefused(
            f"payTo {accept.get('payTo')} != catalogue rail {expected_pay_to}"
        )
    asset = accept.get("asset", "")
    if asset.lower() != USDC_BASE.lower():
        raise PaymentRefused(f"asset {asset} is not Base USDC {USDC_BASE}")
    amount = int(accept.get("amount", "0"))
    return amount / (10**USDC_DECIMALS)


def call_paid(
    resource_id: str,
    private_key: str | None = None,
    max_amount_usd: float = DEFAULT_MAX_AMOUNT_USD,
) -> dict[str, Any]:
    """Execute a paid x402 call to a Blockchain IQ resource.

    Steps: GET -> 402 -> validate amount/asset/payTo against the live
    catalogue -> sign an EIP-3009 authorization for the exact quoted amount
    -> retry with the payment header -> return the resource JSON.

    The key is taken from ``X402_EVM_PRIVATE_KEY`` when not passed directly.
    Refuses when the quoted price exceeds ``max_amount_usd`` (hard cap $0.05)
    or when any catalogue cross-check fails.
    """
    if max_amount_usd > HARD_MAX_AMOUNT_USD:
        raise PaymentRefused(
            f"max_amount_usd ${max_amount_usd} exceeds hard cap "
            f"${HARD_MAX_AMOUNT_USD}"
        )
    key = private_key or os.environ.get("X402_EVM_PRIVATE_KEY")
    if not key:
        raise PaymentRefused(
            "no wallet configured: set X402_EVM_PRIVATE_KEY to run a paid call"
        )

    from eth_account import Account
    from x402 import x402ClientSync
    from x402.http.x402_http_client import x402HTTPClientSync
    from x402.mechanisms.evm.exact.register import register_exact_evm_client
    from x402.mechanisms.evm.signers import EthAccountSigner

    url = get_resource_url(resource_id)
    resp = _get(url)
    if resp.status_code == 200:
        return {"resource": resource_id, "paid": False, "data": resp.json()}
    if resp.status_code != 402:
        resp.raise_for_status()
        raise RuntimeError(f"unexpected status {resp.status_code} for {resource_id}")

    header = resp.headers.get(PAYMENT_REQUIRED_HEADER)
    if not header:
        raise RuntimeError("402 without a Payment-Required header")
    required = _decode_payment_required(header)
    accept = _base_accept(required.get("accepts", []))
    price_usd = _check_accept_against_catalog(accept)
    if price_usd > max_amount_usd:
        raise PaymentRefused(
            f"quoted ${price_usd:.2f} exceeds max_amount_usd ${max_amount_usd:.2f}"
        )

    account = Account.from_key(key)
    signer = EthAccountSigner(account)
    client = x402ClientSync()
    register_exact_evm_client(client, signer, networks=BASE_NETWORK)
    http_client = x402HTTPClientSync(client)

    payment_headers, _payload = http_client.handle_402_response(
        dict(resp.headers), resp.content, url
    )
    # Settlement happens server-side via the facilitator; give it time.
    paid_resp = _get(url, headers=payment_headers, timeout=120)
    paid_resp.raise_for_status()
    return {
        "resource": resource_id,
        "paid": True,
        "price_usd": price_usd,
        "payer": account.address,
        "data": paid_resp.json(),
    }


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------


@tool
def list_blockchainiq_resources() -> str:
    """List Blockchain IQ's x402 resources (free).

    Returns the catalogue: each resource's id, HTTP method, path, price
    (free to $2.00) and description. Use the id with
    get_blockchainiq_resource. The cheapest paid resource is 'brief' ($0.01).
    """
    resources = list_resources()
    return json.dumps(resources, indent=2)


@tool
def get_blockchainiq_resource(resource_id: str, pay: bool = False) -> str:
    """Fetch one Blockchain IQ x402 resource by catalogue id.

    Args:
        resource_id: catalogue id, e.g. 'brief' ($0.01), 'sample' (free).
        pay: when False (default) a paid resource is NOT fetched — the
            decoded 402 payment requirements are returned instead, so the
            agent can inspect the price first. When True, executes the paid
            call, but only if X402_EVM_PRIVATE_KEY is set, only on Base in
            USDC, only to the catalogue-published payTo, and only up to
            $0.01 (override via the X402_MAX_AMOUNT_USD env var, hard cap
            $0.05). The key is never accepted as an argument.
    """
    if not pay:
        return json.dumps(quote_resource(resource_id), indent=2)
    max_usd = float(os.environ.get("X402_MAX_AMOUNT_USD", DEFAULT_MAX_AMOUNT_USD))
    return json.dumps(
        call_paid(resource_id, max_amount_usd=max_usd), indent=2
    )


__all__ = [
    "BASE_URL",
    "CATALOG_URL",
    "list_resources",
    "quote_resource",
    "call_paid",
    "list_blockchainiq_resources",
    "get_blockchainiq_resource",
    "PaymentRefused",
]
