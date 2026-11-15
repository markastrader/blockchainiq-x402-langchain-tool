# Blockchain IQ x402 — LangChain Tool

A LangChain tool that buys market-data resources from
[Blockchain IQ](https://www.blockchainiq.co) through the
[x402](https://www.x402.org) pay-per-call protocol — one paid HTTP call,
$0.01 of USDC on Base, no subscription, no API key.

Built for the **Blockchain IQ x402 build bounty**
([announcement](https://www.blockchainiq.co/blog/x402-build-bounties)):
integrate x402 into an agent framework (LangChain), make a working paid
call to one of two resources, and claim $150 (first passing claim) or $125
(next three). Bounty closes **31 Dec 2026, 11:59 PM Central**. Claims are
checked in arrival order at `partnerships@blockchainiq.co`.

## What it does

| Tool | Cost | What happens |
|---|---|---|
| `list_blockchainiq_resources` | free | Lists the catalogue (33 resources, free–$2.00) |
| `get_blockchainiq_resource(id, pay=False)` | free | Returns the decoded 402 payment requirements (network, amount, asset, payTo) — nothing signed, nothing charged |
| `get_blockchainiq_resource(id, pay=True)` | $0.01 | Executes the paid call to `brief` and returns the market brief JSON |

The cheapest provable paid call is **`brief`** — `GET /api/x402/v1/brief`,
$0.01 USDC on Base (eip155:8453), payTo
`0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e`. It returns BTC/ETH/SOL/XRP
prices, 24h/7d/30d moves, events in the next 48h and risk flags.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Free: list the catalogue
python demo.py --list

# Free: see the 402 quote for 'brief' (decoded Payment-Required header)
python demo.py --resource brief

# Paid: execute the $0.01 call (needs a funded Base wallet)
cp .env.example .env   # put your key in .env, or export X402_EVM_PRIVATE_KEY
X402_EVM_PRIVATE_KEY=0x... python demo.py --resource brief --pay
```

Use it from LangChain:

```python
from blockchainiq_x402_tool import (
    get_blockchainiq_resource,
    list_blockchainiq_resources,
)

print(list_blockchainiq_resources.invoke({}))
print(get_blockchainiq_resource.invoke({"resource_id": "brief"}))          # quote only
print(get_blockchainiq_resource.invoke({"resource_id": "brief", "pay": True}))  # paid
```

## How the paid call works

1. `GET https://www.blockchainiq.co/api/x402/v1/brief` → **HTTP 402** with a
   base64 `Payment-Required` header (x402 v2).
2. The header is decoded and the Base accept is **cross-checked against the
   live catalogue's published payment rails** (asset must be Base USDC
   `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, payTo must match the rail,
   amount must be ≤ the cap).
3. The official `x402` Python SDK (`x402ClientSync` + `ExactEvmScheme`,
   EIP-3009 `transferWithAuthorization`) signs the exact quoted amount and
   retries with the payment header. The client never sends an on-chain
   transaction — the facilitator settles it.
4. The server returns the resource JSON (200).

## Safety rails

- **Free by default.** The demo and the tool both stop at the 402 quote
  unless you explicitly pass `--pay` / `pay=True`.
- **Key from the environment only** (`X402_EVM_PRIVATE_KEY`). Never a tool
  argument, never logged, never written to disk.
- **Amount cap:** $0.01 default, hard cap $0.05 — the tool refuses any
  quote above the cap.
- **Rail check:** amount, asset and payTo are verified against the live
  catalogue before any signature is produced, so a spoofed 402 cannot
  redirect funds.

## Verified live (15 Nov 2026)

- Catalogue: 33 resources, `x402Version: 2`, Base + Solana rails.
- `GET /brief` → HTTP 402, base64 `Payment-Required` header; Base accept:
  amount `10000` (= $0.01, 6 decimals), USDC
  `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (verified on-chain: name
  "USD Coin", symbol USDC, 6 decimals — note: the widely-quoted
  `...029029` ending has NO code on Base; the real address ends
  `...A02913`), payTo
  `0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e`.
- Free `sample` endpoint returns 503 when its figures are >3 days old
  (server-side staleness, not an integration issue).
- `demo.py --list` and `demo.py --resource brief` tested end-to-end free.
- Paid retry with an **unfunded throwaway wallet** returns HTTP 402 again
  (the facilitator rejects the authorization, as expected) — the complete
  code path is proven; only a funded wallet ($0.01 USDC) stands between
  this and a 200 with the brief JSON. The paid call itself is implemented
  and code-reviewed but **not yet executed** — it needs a funded Base
  wallet ($0.01 USDC + nothing else). That is the single remaining step
  before claiming the bounty.

## Bounty claim checklist

- [x] Public repo with a working LangChain tool for a paid x402 resource
- [x] Free path tested (catalogue + 402 quote decode)
- [ ] One working paid call (`brief`, $0.01) — needs a funded wallet
- [ ] Email the claim to `partnerships@blockchainiq.co` with the repo link
      and proof of the paid call (first passing claim: $150)

## License

MIT. Market data is Blockchain IQ's; this tool is an independent
integration, not affiliated with or endorsed by Blockchain IQ.
