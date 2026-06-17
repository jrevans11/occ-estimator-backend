#!/usr/bin/env python3
"""
Reprocess reports that returned 0 items (likely truncated JSON) with a higher max_tokens.
Run this AFTER repair_analyzer.py has completed.

Usage:
    python3 repair_analyzer_retry.py
"""

import os
import csv
import glob
import json
import time
import sys

from pypdf import PdfReader
import anthropic

INPUT_DIR = os.path.expanduser("~/inspection_reports")
OUTPUT_CSV = "repair_items.csv"
MODEL = "claude-sonnet-4-6"
MAX_CHARS_PER_REPORT = 100_000
MAX_TOKENS = 8000  # increased from 4000

def load_env(path=".env"):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_env()

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not found.")
    sys.exit(1)

client = anthropic.Anthropic(api_key=API_KEY)

EXTRACTION_PROMPT = """You are reviewing a residential home inspection report for a construction company that handles closing repairs.

Extract every repair/deficiency item mentioned in the report that would typically require a contractor to fix (ignore "limitations" sections, informational notes, and items marked as fine/satisfactory).

For each repair item, output a JSON object with these fields:
- "category": general category (e.g. "Electrical", "Plumbing", "Roofing", "HVAC", "Exterior", "Interior", "Foundation/Structural", "Windows/Doors", "Insulation", "Appliances", "Other")
- "item": short description of the specific repair needed (e.g. "Replace GFCI outlet in master bathroom")
- "location": room/area if specified (e.g. "Master Bathroom", "Attic", "Crawlspace") or "General" if not specified
- "severity": one of "Safety", "Major", "Minor", "Cosmetic" based on how the report describes it

Respond with ONLY a JSON array of these objects, no other text, no markdown formatting, no code fences. If no repair items are found, respond with an empty array: []

Here is the inspection report text:

"""


def extract_text(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"
    return text[:MAX_CHARS_PER_REPORT]


def get_repair_items(report_text, filename):
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT + report_text}
            ],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("Response was not a JSON array")
        return items
    except json.JSONDecodeError as e:
        print(f"  STILL FAILED: {filename}: {e}")
        return None
    except Exception as e:
        print(f"  ERROR processing {filename}: {e}")
        return None


def main():
    if not os.path.exists(OUTPUT_CSV):
        print(f"ERROR: {OUTPUT_CSV} not found. Run repair_analyzer.py first.")
        sys.exit(1)

    # Find files that have ZERO rows in the existing CSV
    processed_with_items = set()
    with open(OUTPUT_CSV, "r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if row:
                processed_with_items.add(row[0])

    all_pdfs = sorted(glob.glob(os.path.join(INPUT_DIR, "*.pdf")))
    all_filenames = {os.path.basename(p) for p in all_pdfs}

    # Files with no items at all (either failed JSON, or genuinely empty)
    missing = sorted(all_filenames - processed_with_items)

    print(f"Total PDFs: {len(all_filenames)}")
    print(f"Files with at least 1 item already: {len(processed_with_items)}")
    print(f"Files with ZERO items (to retry): {len(missing)}\n")

    if not missing:
        print("Nothing to retry!")
        return

    for f in missing:
        print(f" - {f}")

    print(f"\nRetrying {len(missing)} files with max_tokens={MAX_TOKENS}...\n")

    with open(OUTPUT_CSV, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)

        for i, filename in enumerate(missing, 1):
            pdf_path = os.path.join(INPUT_DIR, filename)
            print(f"Retrying {i}/{len(missing)}: {filename}")

            try:
                text = extract_text(pdf_path)
            except Exception as e:
                print(f"  ERROR reading PDF {filename}: {e}")
                continue

            if not text.strip():
                print(f"  WARNING: No text extracted, skipping")
                continue

            items = get_repair_items(text, filename)

            if items is None:
                print(f"  -> Still failed, skipping")
                continue

            print(f"  -> Found {len(items)} repair items")

            for item in items:
                writer.writerow([
                    filename,
                    item.get("category", ""),
                    item.get("item", ""),
                    item.get("location", ""),
                    item.get("severity", ""),
                ])

            csvfile.flush()
            time.sleep(0.5)

    print("\nDone! Retried results appended to", OUTPUT_CSV)


if __name__ == "__main__":
    main()
