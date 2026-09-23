#!/usr/bin/env python3
"""KSHAPE payout & APR snapshot.

Runs every 15 minutes in GitHub Actions. Reads StonkFun's public API and the Solana chain,
computes APR with the Stonk Board methodology (thestonkboard.com/methodology) and writes
web/data/latest.json, web/data/history.jsonl and web/data/events.json. Standard library only.

If a required input is missing the script exits non-zero and writes nothing: the site keeps
the previous snapshot, which carries its own timestamp, instead of publishing a wrong number.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

MINT = "j9H2npnqabUWksozQL8VmeF5EuAHiHc31bN7LMoyHr7"
API = "https://www.stonkfun.xyz/api/public/v1"
RPC = os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
MIN_USD = 20.0                       # Board eligibility floor
WINDOWS = {"h24": 24, "d3": 72, "d7": 168}
DATA = pathlib.Path(__file__).resolve().parent.parent / "web" / "data"
UTC = dt.timezone.utc


# ------------------------------------------------------------------ transport
def http_json(url, body=None, tries=8):
    data = json.dumps(body).encode() if body is not None else None
    wait = 1.5
    for _ in range(tries):
        req = urllib.request.Request(url, data=data, headers={
            "accept": "application/json", "content-type": "application/json",
            "user-agent": "kshape.xyz/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(wait); wait = min(wait * 1.8, 30); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(wait); wait = min(wait * 1.8, 30)
    raise RuntimeError(f"gave up on {url}")


def api(path):
    return http_json(API + path)["data"]


def rpc(method, params):
    out = http_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


# ------------------------------------------------- ed25519 on-curve test
# The Board excludes off-curve owners: program addresses such as the curve and pools.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def on_curve(pubkey):
    n = 0
    for c in pubkey:
        n = n * 58 + _B58.index(c)
    y = int.from_bytes(n.to_bytes(32, "big"), "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


# ------------------------------------------------------------ helpers
def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def apy(apr):
    """The Board's formula: one reinvestment a day at a constant APR."""
    try:
        return 100 * ((1 + apr / 36500) ** 365 - 1)
    except OverflowError:
        return float("inf")


def token_deltas(tx):
    """{(owner, mint): raw delta} for every token balance the transaction touched."""
    meta, out = tx["meta"], {}
    pre = {(b["accountIndex"], b["mint"]): b for b in meta.get("preTokenBalances") or []}
    post = {(b["accountIndex"], b["mint"]): b for b in meta.get("postTokenBalances") or []}
    for k in set(pre) | set(post):
        b = post.get(k) or pre.get(k)
        d = (int(post[k]["uiTokenAmount"]["amount"]) if k in post else 0) - \
            (int(pre[k]["uiTokenAmount"]["amount"]) if k in pre else 0)
        key = (b.get("owner"), k[1])
        out[key] = out.get(key, 0) + d
    return out


def instruction_types(tx):
    ixs = list(tx["transaction"]["message"]["instructions"])
    for g in tx["meta"].get("innerInstructions") or []:
        ixs += g["instructions"]
    return {ix["parsed"]["type"] for ix in ixs
            if isinstance(ix.get("parsed"), dict) and "type" in ix["parsed"]}



# ------------------------------------------------- the K: NVDA vs real wages
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/NVDA?range=7y&interval=1wk"
BLS = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
WAGES = "CES0500000013"        # avg hourly earnings, total private, 1982-84 dollars (real)


def refresh_k_series(path, max_age_h=20):
    """Weekly NVDA closes and monthly real wages, both indexed to Jan 2020 = 100.

    Refreshed once a day: BLS limits unregistered callers and neither feed is ours. Any
    failure leaves the previous file in place — the K chart goes stale, the rest of the
    site does not.
    """
    if path.exists():
        try:
            prev = json.loads(path.read_text())
            if (dt.datetime.now(UTC) - ts(prev["generated_at"])).total_seconds() < max_age_h * 3600:
                return prev
        except Exception:
            prev = None
    y = http_json(YAHOO, tries=3)["chart"]["result"][0]
    stamps = y["timestamp"]
    closes = (y["indicators"].get("adjclose") or [{}])[0].get("adjclose") or y["indicators"]["quote"][0]["close"]
    nvda = [[dt.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"), c]
            for t, c in zip(stamps, closes) if c]
    b = http_json(BLS, {"seriesid": [WAGES], "startyear": "2019", "endyear": str(now_year())}, tries=3)
    if b.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {b.get('status')} {b.get('message')}")
    wages = []
    for d in b["Results"]["series"][0]["data"]:
        try:                                                 # BLS writes "-" for months it has not published
            if d["period"].startswith("M"):
                wages.append([f"{d['year']}-{d['period'][1:]}-01", float(d["value"])])
        except ValueError:
            continue
    wages.sort()
    base_n = next(v for d, v in nvda if d >= "2020-01-01")
    base_w = next(v for d, v in wages if d >= "2020-01-01")
    out = {"generated_at": iso(dt.datetime.now(UTC)),
           "base": "2020-01 = 100",
           "series": {"nvda": [[d, round(v / base_n * 100, 2)] for d, v in nvda],
                      "wages": [[d, round(v / base_w * 100, 2)] for d, v in wages]},
           "latest": {"nvda_usd": nvda[-1][1], "nvda_date": nvda[-1][0],
                      "wages_index": wages[-1][1], "wages_date": wages[-1][0]},
           "sources": {"nvda": "Yahoo Finance, weekly close, split-adjusted",
                       "wages": "BLS CES0500000013 — average hourly earnings, total private, 1982-84 dollars"}}
    path.write_text(json.dumps(out))
    return out


def now_year():
    return dt.datetime.now(UTC).year


# --------------------------------------------------------------- main
def main():
    now = dt.datetime.now(UTC)
    DATA.mkdir(parents=True, exist_ok=True)

    # StonkFun: prices, launch time, payouts
    tok = api(f"/tokens/{MINT}")
    token, launch = tok["token"], tok["launch"]
    quote_mint = token["quote"]["mint"]
    launch_at = ts(launch["createdAt"])
    rw = api(f"/tokens/{MINT}/rewards")
    rewards, qdec = rw["rewards"], rw["quote"]["decimals"]
    px_quote = api(f"/launchlab/pricing?quoteMint={quote_mint}")["prices"]["quoteUsd"]
    # xStocks scale balances by a multiplier that accrues dividends: shares = raw x multiplier
    qmint = rpc("getAccountInfo", [quote_mint, {"encoding": "jsonParsed"}])["value"]["data"]["parsed"]["info"]
    scale = 1.0
    for e in qmint.get("extensions", []):
        if e["extension"] == "scaledUiAmountConfig":
            st = e["state"]                                  # the pending multiplier takes over at its timestamp
            scale = float(st["newMultiplier"]) if int(st["newMultiplierEffectiveTimestamp"]) <= now.timestamp() \
                else float(st["multiplier"])
    px_token = token["market"]["priceUsd"]
    if not (px_token and px_quote and rewards):
        raise RuntimeError("StonkFun returned an empty price or rewards object")

    # Mint: supply, decimals, fee config, withheld on the mint
    mint = rpc("getAccountInfo", [MINT, {"encoding": "jsonParsed"}])["value"]["data"]["parsed"]["info"]
    dec = mint["decimals"]
    supply = int(mint["supply"]) / 10 ** dec
    fee_cfg = next(e["state"] for e in mint["extensions"] if e["extension"] == "transferFeeConfig")
    platform = fee_cfg["withdrawWithheldAuthority"]
    on_mint = int(fee_cfg["withheldAmount"]) / 10 ** dec
    fee_bps = fee_cfg["newerTransferFee"]["transferFeeBasisPoints"]

    # Every holder account: balances, unharvested withheld fees, frozen state
    accts = rpc("getProgramAccounts", [TOKEN_2022, {"encoding": "jsonParsed",
                "filters": [{"memcmp": {"offset": 0, "bytes": MINT}}]}])
    if not accts:
        raise RuntimeError("getProgramAccounts returned no holders")
    owners, in_accounts = {}, 0.0
    for a in accts:
        info = a["account"]["data"]["parsed"]["info"]
        for e in info.get("extensions", []):
            if e["extension"] == "transferFeeAmount":
                in_accounts += int(e["state"]["withheldAmount"]) / 10 ** dec
        if info.get("state") == "frozen":
            continue
        amt = int(info["tokenAmount"]["amount"]) / 10 ** dec
        owners[info["owner"]] = owners.get(info["owner"], 0.0) + amt

    min_tokens = MIN_USD / px_token
    eligible = {o: a for o, a in owners.items()
                if a >= min_tokens and o != platform and on_curve(o)}
    eligible_tokens = sum(eligible.values())
    if eligible_tokens <= 0:
        raise RuntimeError("no eligible holdings")

    # The platform's KSHAPE account: fees withdrawn in, sold to the quote token out
    pacct = rpc("getTokenAccountsByOwner", [platform, {"mint": MINT}, {"encoding": "jsonParsed"}])["value"]
    pacct = pacct[0]["pubkey"]
    ev_path = DATA / "events.json"
    ev = json.loads(ev_path.read_text()) if ev_path.exists() else \
        {"account": pacct, "last_sig": None, "fees": [], "conversions": []}
    new, before = [], None
    while True:
        opts = {"limit": 1000}
        if ev["last_sig"]:
            opts["until"] = ev["last_sig"]
        if before:
            opts["before"] = before
        page = rpc("getSignaturesForAddress", [pacct, opts])
        new += page
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    for s in reversed(new):                                  # oldest first
        if s.get("err"):
            continue
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                                                     "maxSupportedTransactionVersion": 0}])
        if not tx or tx["meta"].get("err"):
            continue
        t = iso(dt.datetime.fromtimestamp(tx["blockTime"], UTC))
        deltas = token_deltas(tx)
        d_tok = deltas.get((platform, MINT), 0) / 10 ** dec
        if d_tok > 0 and any("ithheld" in x for x in instruction_types(tx)):
            ev["fees"].append([t, d_tok, s["signature"]])
        elif d_tok < 0:
            got = deltas.get((platform, quote_mint), 0)
            if got > 0:
                ev["conversions"].append([t, got / 10 ** qdec, s["signature"]])
        time.sleep(0.15)
    if new:
        ev["last_sig"] = new[0]["signature"]

    withdrawn = sum(f[1] for f in ev["fees"])
    assessed = withdrawn + on_mint + in_accounts
    bought = sum(c[1] for c in ev["conversions"])

    # Operating deduction, measured: what holders received out of what the tax bought,
    # counting only conversions the last payout could have included.
    paid, pending = rewards["distributedTokens"], rewards["undistributedTokens"]
    last_payout = ts(rewards["lastPayoutAt"]) if rewards.get("lastPayoutAt") else None
    base = sum(c[1] for c in ev["conversions"] if last_payout and ts(c[0]) <= last_payout)
    pass_through = paid / base if base else None
    deduction = min(max(1 - pass_through, 0.0), 0.5) if pass_through else 0.0

    days = (now - launch_at).total_seconds() / 86400

    def apr_for(fees_in_window, window_days):
        return fees_in_window * (1 - deduction) / window_days / eligible_tokens * 365 * 100

    apr = {"since_launch": apr_for(assessed, days)}
    end = now.replace(minute=0, second=0, microsecond=0)     # complete hours, as the Board does
    for key, hours in WINDOWS.items():
        start = end - dt.timedelta(hours=hours)
        if start < launch_at:
            apr[key] = None                                   # n/a: not enough history yet
            continue
        inwin = sum(f[1] for f in ev["fees"] if start < ts(f[0]) <= end)
        apr[key] = apr_for(inwin, hours / 24)
    delivered_usd = (paid + pending) * px_quote
    apr["realized_since_launch"] = delivered_usd / days / (eligible_tokens * px_token) * 365 * 100

    # Chart: the quote token bought with the tax, cumulative and per hour
    cum, running, hourly = [], 0.0, {}
    for t, amt, sig in ev["conversions"]:
        running += amt
        cum.append([t, round(running, 8), sig])
        h = t[:13] + ":00:00Z"
        hourly[h] = hourly.get(h, 0.0) + amt

    snap = {
        "generated_at": iso(now),
        "launch_at": iso(launch_at),
        "hours_since_launch": round(days * 24, 2),
        "token": {"mint": MINT, "symbol": token["symbol"], "price_usd": px_token,
                  "market_cap_usd": token["market"]["marketCapUsd"],
                  "peak_market_cap_usd": token["market"].get("peakMarketCapUsd"),
                  "status": token["status"], "graduation_progress": token.get("graduationProgress"),
                  "transfer_fee_bps": fee_bps, "supply": supply, "pool": token.get("pool")},
        "quote": {"mint": quote_mint, "symbol": token["quote"]["symbol"], "price_usd": px_quote,
                  "share_multiplier": scale, "tokens_per_share": 1 / scale},
        "payouts": {"paid": paid, "pending": pending, "paid_usd": paid * px_quote,
                    "shares_paid": paid * scale, "shares_bought": bought * scale,
                    "token_per_share": (1 / scale) / px_token * px_quote,
                    "payout_count": rewards["payoutCount"], "wallets_paid": rewards["holderCount"],
                    "last_payout_at": rewards.get("lastPayoutAt")},
        "fees": {"assessed": assessed, "pct_of_supply": assessed / supply * 100,
                 "withdrawn": withdrawn, "on_mint": on_mint, "in_accounts": in_accounts,
                 "quote_bought": bought, "platform_account": pacct, "platform_wallet": platform},
        "deduction": {"measured": deduction, "pass_through": pass_through,
                      "base_bought": base},
        "holders": {"owners": len(owners), "eligible_wallets": len(eligible),
                    "eligible_tokens": eligible_tokens, "eligible_usd": eligible_tokens * px_token,
                    "min_tokens": min_tokens, "min_usd": MIN_USD},
        "apr": apr,
        "apy": {k: (apy(v) if v is not None else None) for k, v in apr.items()},
        "series": {"bought_cumulative": cum,
                   "bought_hourly": sorted([h, round(v, 8)] for h, v in hourly.items())},
    }
    # JSON has no infinity
    snap["apy"] = {k: (None if v is None else ("inf" if v == float("inf") else v))
                   for k, v in snap["apy"].items()}

    try:
        refresh_k_series(DATA / "k.json")
    except Exception as e:                                   # the K chart may go stale; nothing else does
        print(f"k-series refresh skipped: {e}", file=sys.stderr)

    ev_path.write_text(json.dumps(ev, indent=1))
    (DATA / "latest.json").write_text(json.dumps(snap, indent=1))
    with open(DATA / "history.jsonl", "a") as h:
        h.write(json.dumps({"t": snap["generated_at"], "paid": paid, "pending": pending,
                            "apr": apr, "eligible_wallets": len(eligible),
                            "eligible_usd": snap["holders"]["eligible_usd"],
                            "assessed": assessed, "px_token": px_token, "px_quote": px_quote,
                            "deduction": deduction}) + "\n")
    print(json.dumps({k: snap[k] for k in ("generated_at", "payouts", "apr", "deduction")}, indent=1))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                   # fail loudly, write nothing
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
