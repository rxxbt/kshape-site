# kshape.xyz

[![update](https://github.com/rxxbt/kshape-site/actions/workflows/update.yml/badge.svg)](https://github.com/rxxbt/kshape-site/actions/workflows/update.yml)

Live at **[kshape.xyz](https://kshape.xyz)**.

Live NVDAX payouts and APR for [$KSHAPE](https://www.stonkfun.xyz/token/j9H2npnqabUWksozQL8VmeF5EuAHiHc31bN7LMoyHr7),
calculated with the [Stonk Board methodology](https://thestonkboard.com/methodology).

Every 15 minutes a GitHub Actions job runs `compute/kshape.py`, which reads StonkFun's public API
and the Solana chain, writes a snapshot to `web/data/`, commits it, and deploys `web/` to GitHub Pages.
Every snapshot is in this repository's history.

| File | What it holds |
|---|---|
| `web/data/latest.json` | the current snapshot the site renders |
| `web/data/history.jsonl` | one line per run: payouts, APR by window, eligible holdings, prices |
| `web/data/events.json` | every fee withdrawal and every conversion to NVDAX, with its transaction signature |

How each number is calculated, and where it differs from the Board: [kshape.xyz/methodology](https://kshape.xyz/methodology.html).

Run it yourself (Python 3, standard library only):

```
python3 compute/kshape.py          # set RPC_URL to use your own Solana RPC
```
