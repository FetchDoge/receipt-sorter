#!/usr/bin/env python3
"""Local-first receipt and invoice sorter.

Reads receipt files from a folder, asks a local model served by Ollama
to extract structured data as strict JSON, validates and normalizes
that data, removes duplicates, and writes a ledger CSV in which every
row carries provenance.
"""
import argparse
import base64
import csv
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

CATEGORIES = [
    "Groceries", "Dining", "Transport", "Fuel", "Software", "Hardware",
    "Office Supplies", "Utilities", "Travel", "Lodging",
    "Professional Services", "Other",
]
CATEGORY_LOOKUP = {name.lower(): name for name in CATEGORIES}
TEXT_SUFFIXES = {".txt", ".md"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES
CSV_COLUMNS = [
    "date", "vendor", "amount", "currency", "category",
    "source_file", "model", "extracted_at", "status", "notes",
]
DEFAULT_MODEL = "gemma3:1b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 600

def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Sort receipts and invoices from a folder into a "
        "ledger CSV using a local model served by Ollama."
    )
    parser.add_argument("input_folder", help="Folder containing receipt files.")
    parser.add_argument("--out", default="ledger.csv",
                        help="Output CSV path (default: ledger.csv).")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model name to request (default: {DEFAULT_MODEL}).")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL,
                        help=f"Ollama base URL (default: {DEFAULT_OLLAMA_URL}).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    return parser.parse_args(argv)

def utc_now_iso():
    """Return the current UTC time in ISO 8601 format (seconds)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def build_prompt(receipt_text):
    """Build the extraction prompt, optionally embedding receipt text."""
    lines = [
        "Extract structured data from the receipt.",
        "Respond with ONLY a JSON object, with no other text.",
        "The JSON object must have exactly these keys: "
        "vendor, date, amount, currency, category.",
        "Rules:",
        "- date: the receipt date formatted as YYYY-MM-DD.",
        "- amount: a plain number, the receipt total, with no "
        "currency symbol and no thousands separators.",
        "- currency: a 3-letter currency code such as USD, CAD, EUR, or GBP.",
        f"- category: MUST be one of: {', '.join(CATEGORIES)}.",
        "- If a value is not on the receipt, use null for that value. "
        "Never guess.",
    ]
    if receipt_text is not None:
        lines += ["Receipt text:", receipt_text]
    else:
        lines.append("The receipt is provided as an attached image.")
    return "\n".join(lines)

def check_server(ollama_url, timeout):
    """Raise an error if the Ollama server cannot be reached."""
    url = ollama_url.rstrip("/") + "/api/tags"
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()

def parse_model_json(text):
    """Parse model output as JSON, falling back to the first object."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start = text.find("{") if isinstance(text, str) else -1
        end = text.rfind("}") if isinstance(text, str) else -1
        if start == -1 or end <= start:
            raise ValueError("model output contained no JSON object")
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"model output was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("model output was not a JSON object")
    return value

def call_ollama(ollama_url, model, prompt, timeout, image_b64=None):
    """Call the Ollama generate API and return the parsed JSON object."""
    payload = {
        "model": model, "prompt": prompt, "stream": False,
        "format": "json", "options": {"temperature": 0},
    }
    if image_b64 is not None:
        payload["images"] = [image_b64]
    request = urllib.request.Request(
        ollama_url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    try:
        outer = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ollama response was not valid JSON: {exc}") from exc
    if not isinstance(outer, dict) or "response" not in outer:
        raise ValueError("Ollama response had no 'response' field")
    return parse_model_json(outer["response"])

def extract_for_file(path, args):
    """Extract a raw record for one receipt file."""
    if path.suffix.lower() in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        return call_ollama(args.ollama_url, args.model,
                           build_prompt(text), args.timeout)
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return call_ollama(args.ollama_url, args.model, build_prompt(None),
                       args.timeout, image_b64=image_b64)

def normalize_date(value):
    """Validate a date; return (csv_value, problem_or_None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", "missing date"
    if not isinstance(value, str):
        return "", "invalid date"
    text = value.strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return "", "invalid date"
    return (text, None) if parsed.isoformat() == text else ("", "invalid date")

def normalize_amount(value):
    """Validate an amount; return (csv_value, problem_or_None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", "missing amount"
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return "", "invalid amount"
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return "", "invalid amount"
    if not amount.is_finite() or amount <= 0:
        return "", "invalid amount"
    return format(amount, ".2f"), None

def normalize_currency(value):
    """Validate a currency; return (csv_value, problem_or_None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", "missing currency"
    if not isinstance(value, str):
        return "", "invalid currency"
    code = value.strip().upper()
    return (code, None) if re.fullmatch(r"[A-Z]{3}", code) else ("", "invalid currency")

def normalize_category(value):
    """Validate a category; return (csv_value, problem_or_None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", "missing category"
    if not isinstance(value, str):
        return "", "invalid category"
    canonical = CATEGORY_LOOKUP.get(value.strip().lower())
    return (canonical, None) if canonical else ("", "invalid category")

def normalize_vendor(value):
    """Validate a vendor; return (csv_value, problem_or_None)."""
    if value is None or not isinstance(value, str):
        return "", "missing vendor"
    collapsed = " ".join(value.split())
    return (collapsed, None) if collapsed else ("", "missing vendor")

def validate_record(raw):
    """Validate and normalize a raw record without repairing values."""
    fields, problems = {}, []
    checks = [
        ("vendor", normalize_vendor(raw.get("vendor"))),
        ("date", normalize_date(raw.get("date"))),
        ("amount", normalize_amount(raw.get("amount"))),
        ("currency", normalize_currency(raw.get("currency"))),
        ("category", normalize_category(raw.get("category"))),
    ]
    for name, (csv_value, problem) in checks:
        fields[name] = csv_value
        if problem is not None:
            problems.append(problem)
    return fields, problems

def make_row(fields, source_file, model, problems):
    """Assemble a ledger row from normalized fields and provenance."""
    return {
        "date": fields.get("date", ""), "vendor": fields.get("vendor", ""),
        "amount": fields.get("amount", ""), "currency": fields.get("currency", ""),
        "category": fields.get("category", ""), "source_file": source_file,
        "model": model, "extracted_at": utc_now_iso(),
        "status": "needs_review" if problems else "ok",
        "notes": "; ".join(problems),
    }

def error_row(source_file, model, message):
    """Build a needs_review row for a file that could not be extracted."""
    empty = {name: "" for name in ("date", "vendor", "amount", "currency", "category")}
    return make_row(empty, source_file, model, [message])

def dedupe_rows(rows):
    """Drop duplicate rows, keeping the first occurrence of each key."""
    seen, kept, skipped = set(), [], 0
    for row in rows:
        key = (row["vendor"].lower(), row["date"], row["amount"], row["currency"])
        if all(key) and key in seen:
            skipped += 1
            continue
        if all(key):
            seen.add(key)
        kept.append(row)
    return kept, skipped

def sort_rows(rows):
    """Sort rows by (date, vendor), with empty dates last."""
    return sorted(rows, key=lambda r: (r["date"] == "", r["date"], r["vendor"].lower()))

def write_csv(rows, out_path):
    """Write ledger rows to a CSV file."""
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in CSV_COLUMNS})

def discover_files(input_folder):
    """Return supported receipt files in sorted filename order."""
    files = [p for p in input_folder.iterdir()
             if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]
    return sorted(files, key=lambda p: p.name)

def main(argv=None):
    """Run the receipt sorter."""
    args = parse_args(argv)
    input_folder = Path(args.input_folder)
    if not input_folder.is_dir():
        print(f"error: input folder not found: {input_folder}", file=sys.stderr)
        return 2
    try:
        check_server(args.ollama_url, args.timeout)
    except Exception as exc:  # fail loudly on any cause
        print(f"error: could not reach Ollama server at {args.ollama_url} "
              f"({exc}); no ledger was written.", file=sys.stderr)
        return 1
    files = discover_files(input_folder)
    rows, errors = [], []
    for path in files:
        try:
            raw = extract_for_file(path, args)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            message = f"extraction failed: {exc}"
            errors.append((path.name, message))
            rows.append(error_row(path.name, args.model, message))
            continue
        fields, problems = validate_record(raw)
        rows.append(make_row(fields, path.name, args.model, problems))
    rows, duplicates_skipped = dedupe_rows(rows)
    rows = sort_rows(rows)
    write_csv(rows, Path(args.out))
    ok_count = sum(1 for r in rows if r["status"] == "ok")
    review_count = sum(1 for r in rows if r["status"] == "needs_review")
    print(f"Files processed: {len(files)}")
    print(f"Rows written: {len(rows)}")
    print(f"OK: {ok_count}")
    print(f"Needs review: {review_count}")
    print(f"Duplicates skipped: {duplicates_skipped}")
    if errors:
        print("Per-file errors:")
        for name, message in errors:
            print(f"  {name}: {message}")
    else:
        print("Per-file errors: none")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
