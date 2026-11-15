#!/usr/bin/env python3
"""Demo CLI for the Blockchain IQ x402 LangChain tool.

Free by default — it only ever performs the 402 handshake and prints the
decoded payment requirements. Nothing is signed and nothing is charged
unless you pass --pay AND set X402_EVM_PRIVATE_KEY.

Examples:
    python demo.py --list                  # free catalogue (33 resources)
    python demo.py --resource brief        # free: 402 quote for 'brief' ($0.01)
    python demo.py --resource sample       # free resource, returns data (may 503)
    X402_EVM_PRIVATE_KEY=0x... python demo.py --resource brief --pay
                                            # paid call: $0.01 USDC on Base
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from blockchainiq_x402_tool import (
    PaymentRefused,
    call_paid,
    list_resources,
    quote_resource,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list the catalogue")
    parser.add_argument(
        "--resource", default="brief", help="catalogue id (default: brief)"
    )
    parser.add_argument(
        "--pay",
        action="store_true",
        help="execute the paid call (needs X402_EVM_PRIVATE_KEY)",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=float(os.environ.get("X402_MAX_AMOUNT_USD", 0.01)),
        help="max USD to pay (default 0.01, hard cap 0.05)",
    )
    args = parser.parse_args()

    try:
        if args.list:
            resources = list_resources()
            print(f"{len(resources)} resources:")
            for r in resources:
                print(f"  {r['id']:<32} {r['price']:<7} {r['title']}")
            return 0
        if args.pay:
            result = call_paid(args.resource, max_amount_usd=args.max_usd)
        else:
            result = quote_resource(args.resource)
        print(json.dumps(result, indent=2))
        return 0
    except PaymentRefused as exc:
        print(f"REFUSED (no charge): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - demo should show the error plainly
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
