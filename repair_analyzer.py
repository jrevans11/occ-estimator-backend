#!/usr/bin/env python3
"""
Repair Pricing Library Analyzer
---------------------------------
Scans a folder of inspection report PDFs, extracts repair/deficiency
line items via Claude, and appends them to a single CSV for building
the OCC repair pricing library.

Usage:
    python3 repair_analyzer.py

Reads ANTHROPIC_API_KEY from a .env file in the same directory (or
from the environment).

Folder of PDFs to scan is set by INPUT_DIR below.
"""

import os
import csv
import glob
import json
import time
import sys
from pathlib import Path

from pypdf import PdfReader
import anthropic

# ---- CONFIG ----
INPUT_DIR = os.path.expanduser("~/inspection_reports")
OUTPUT_CSV = "repair_items.csv"
MODEL = "claude-sonnet-4-6"
MAX_CHARS_PER_REPORT = 100_000  # truncate very long reports to stay within limits

# Load API key from .env if present
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
    print("ERROR: ANTHROPIC_API_KEY not found in environment or .env file.")
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
            max_tokens=4000,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT + report_text}
            ],
        )
        raw = response.content[0].text.strip()

        # Strip accidental code fences if present
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
        print(f"  WARNING: Could not parse JSON for {filename}: {e}")
        return []
    except Exception as e:
        print(f"  ERROR processing {filename}: {e}")
        return []


def main():
    pdf_files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.pdf")))
    if not pdf_files:
        print(f"No PDF files found in {INPUT_DIR}")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDF files in {INPUT_DIR}")
    print(f"Output will be written to {OUTPUT_CSV}\n")

    write_header = not os.path.exists(OUTPUT_CSV)

    with open(OUTPUT_CSV, "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            writer.writerow(["source_file", "category", "item", "location", "severity"])

        for i, pdf_path in enumerate(pdf_files, 1):
            filename = os.path.basename(pdf_path)
            print(f"Processing {i}/{len(pdf_files)}: {filename}")

            try:
                text = extract_text(pdf_path)
            except Exception as e:
                print(f"  ERROR reading PDF {filename}: {e}")
                continue

            if not text.strip():
                print(f"  WARNING: No text extracted from {filename}, skipping")
                continue

            items = get_repair_items(text, filename)
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

            # Small delay to be gentle on rate limits
            time.sleep(0.5)

    print(f"\nDone! Results written to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
