# Demo Results — Receipt Sorter end-to-end run

**Run:** 2026-10-13 ~08:29–08:32 UTC
**Command:** `python3 receiptsort.py fixtures --model gemma3:4b --out demo/ledger.csv`
**Outcome:** exit 0 · 3 files processed · 3 rows written · 3 ok · 0 needs_review · 0 duplicates skipped · 0 per-file errors · wall time 2m58s (model already loaded)
**Outputs:** `demo/ledger.csv` (the ledger), `demo/run.log` (CLI stdout)

## Environment

- VM: 2 CPUs, ~7 GB RAM, ~7.4 GB free disk, Python 3.12
- Ollama 0.40.3, installed locally, server on 127.0.0.1:11434
- Models: gemma3:1b (806 MB) and gemma3:4b (3.35 GB), both Gemma, both run fully locally

## Two infrastructure findings (both handled, both verified)

1. **`ollama pull` does not work on this VM.** Its egress DNS interception resolves public hosts into a non-public 198.18.x.x range, and Ollama's pull client refuses blob redirects to non-public IPs (`Error: redirect target not allowed: ...r2.cloudflarestorage.com resolves to non-public 198.18.154.10`). Workaround used: fetched each model manifest and blob directly over HTTPS from the Ollama registry, verified every blob's SHA-256 against its manifest digest, and assembled Ollama's standard on-disk layout (blobs + manifests) by hand. All 9 blobs across both models are hash-verified.
2. **gemma3:1b cannot read images in Ollama's packaging.** Its manifest has no projector layer; `/api/tags` lists it as `completion` only, and an image request is rejected: `400 — "Multimodal data provided, but model does not support multimodal requests."` gemma3:4b ships a projector layer and lists `['completion', 'vision']`, so the demo was run with `--model gemma3:4b`. A standalone vision probe before the run read the grocery receipt image correctly ("GREEN VALLEY GROCERY / $47.83", ~175 s cold including model load).

## Score vs ground truth (`fixtures/ground-truth.json`)

5 fields scored per receipt (vendor, date, amount, currency, category) — **15/15 field hits, 3/3 receipts fully correct.**

| Source file | Vendor | Date | Amount | Currency | Category |
|---|---|---|---|---|---|
| invoice-software.txt | Inkwell Design Tools ✓ | 2026-10-08 ✓ | 29.00 ✓ | USD ✓ | Software ✓ |
| receipt-grocery.png (image) | GREEN VALLEY GROCERY ✓ | 2026-10-11 ✓ | 47.83 ✓ | USD ✓ | Groceries ✓ |
| receipt-cab.txt | METRO CAB ✓ | 2026-10-12 ✓ | 23.50 ✓ | CAD ✓ | Transport ✓ |

## Honest notes

- **Vendor casing:** for the two receipts printed in all caps, the model returned the vendor exactly as printed ("GREEN VALLEY GROCERY", "METRO CAB") instead of the ground truth's title case. Vendor comparison was case-insensitive and these are counted as hits; the extracted values are faithful to the source documents.
- **Sample size:** 3 fixtures with clean, legible inputs (one rendered image, two typed text receipts). This proves the pipeline end to end; it is not evidence about accuracy on photographed, crumpled, real-world receipts.
- **Speed:** on this 2-core VM the warm 3-file run took 2m58s total; a cold single image call took ~175 s including model load. A 1B text-only model is faster but cannot see images at all in this packaging — that trade-off is real and documented above, not hidden.
- **No fabrication:** every value in `demo/ledger.csv` came from the model's JSON output passed through the CLI's validation layer; nothing was hand-filled.
