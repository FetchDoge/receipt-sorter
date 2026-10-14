# Receipt Sorter

A small, local-first CLI that turns a folder of receipts and invoices into a clean ledger spreadsheet — with provenance on every row and a validation layer that refuses to guess.

Receipt Sorter is written in Python using only the standard library. Point it at a folder of receipts and invoices, and it produces a single file — `ledger.csv` — that you can open in any spreadsheet app.

A local open-weight model (Gemma, run locally through Ollama) reads each document. Images go through the model's vision; plain-text invoices are read directly. For each document it extracts five fields: vendor, date, amount, currency, and category. Nothing is uploaded anywhere.

## What makes this one different

Most receipt tools sell extraction. This one sells the audit trail around the extraction:

- **Provenance on every row.** Each ledger row records its source file, the model that read it, and the extraction timestamp. Any number in the ledger can be traced back to the document it came from.
- **Validation, not vibes.** Dates must be real ISO dates, amounts must be positive, currency must be a 3-letter code, and category must come from a fixed list of 12. Anything missing or invalid is flagged `needs_review` with the reason — the tool never guesses a value to fill a cell.
- **Duplicates are skipped**, defined strictly: same vendor, date, amount, and currency.
- **Hash-verified toolchain.** The build notes in `demo/RESULTS.md` describe verifying every model file's SHA-256 against its manifest before use. A tool that records where values came from should hold its own downloads to the same standard.

## Verified demo (sample fixtures, not real receipts)

The demo in `demo/` ran on 2026-10-13 against three sample fixtures created for testing — one rendered grocery receipt (image), one software invoice (text), one cab receipt (text) — scored against `fixtures/ground-truth.json`:

- **15 of 15 fields correct** (5 fields × 3 fixtures), 0 rows flagged for review
- Model: `gemma3:4b`, fully local via Ollama, on a 2-CPU VM
- Warm wall time for the 3-file run: 2m58s. A cold single-image call took ~175 seconds including model load. Small local models are slow; the results file says so plainly.

That is a pipeline proof on clean fixtures — not a real-world accuracy claim. Messier photographed receipts are the honest next test, and the numbers will be published when it happens.

## Why local?

Receipts are financial documents. They show who was paid, how much, and when. Cloud receipt services work by uploading those documents to someone else's servers. Receipt Sorter is designed so they never have to leave the machine:

- **Private by design.** Documents stay on your machine, under your control.
- **No per-seat API costs.** No cloud extraction bill, no subscription to read your own documents.
- **Works offline**, once the model is downloaded.

## Status

Built and fixture-tested. Not yet released publicly; this package is staged for the maintainer's release decision.

## License

MIT — see `LICENSE`.
