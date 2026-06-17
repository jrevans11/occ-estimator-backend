#!/usr/bin/env python3
"""
Cluster raw repair-item descriptions into standardized repair types per category,
producing a frequency-ranked list ready for the pricing library spreadsheet.

Usage:
    python3 cluster_repair_items.py

Input:  repair_items.csv (in current directory)
Output: pricing_library.csv  (standardized_type, category, frequency, example_items)
"""

import os
import csv
import json
import sys
import time
from collections import defaultdict, Counter

import anthropic

INPUT_CSV = "repair_items.csv"
OUTPUT_CSV = "pricing_library.csv"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000


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

CLUSTER_PROMPT = """You are organizing a repair pricing library for a residential construction/closing-repairs company.

Below is a list of repair items (with counts of how many times similar phrasing appeared) from home inspection reports, all in the category: {category}

Your job: group these into a manageable set of STANDARDIZED REPAIR TYPES (aim for roughly 10-25 standardized types depending on variety -- fewer if items are very similar, more if genuinely distinct work types exist). Each standardized type should be a short, clear description of a billable repair task (e.g. "Replace GFCI outlet", "Repair/replace damaged siding", "Service HVAC unit", "Seal foundation vent").

For each standardized type, sum up the total frequency of all raw items that map to it, and list 2-3 example raw item descriptions.

Respond with ONLY a JSON array, no other text, no markdown fences:
[
  {{"standardized_type": "...", "total_frequency": N, "examples": ["...", "...", "..."]}},
  ...
]

Raw items with counts (format: count | item text):
{items_list}
"""


def cluster_category(category, item_counts):
    """item_counts: list of (item_text, count) tuples"""
    items_list = "\n".join(f"{cnt} | {item}" for item, cnt in item_counts)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "user", "content": CLUSTER_PROMPT.format(category=category, items_list=items_list)}
        ],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found in current directory.")
        sys.exit(1)

    # Load and group by category
    by_category = defaultdict(Counter)
    with open(INPUT_CSV, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) < 3:
                continue
            category, item = row[1], row[2]
            by_category[category][item] += 1

    categories = sorted(by_category.keys(), key=lambda c: -sum(by_category[c].values()))
    print(f"Found {len(categories)} categories")

    write_header = not os.path.exists(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="") as out:
        writer = csv.writer(out)
        if write_header:
            writer.writerow(["category", "standardized_repair_type", "frequency", "example_items", "est_hours", "material_cost", "sub_cost", "notes"])

        for category in categories:
            counter = by_category[category]
            total_items = sum(counter.values())
            print(f"\nProcessing category: {category} ({total_items} total mentions, {len(counter)} unique)")

            # Sort by frequency, take most common items (cap list size to keep prompt manageable)
            item_counts = counter.most_common(150)

            try:
                clusters = cluster_category(category, item_counts)
            except Exception as e:
                print(f"  ERROR clustering {category}: {e}")
                continue

            print(f"  -> {len(clusters)} standardized types")

            for c in clusters:
                writer.writerow([
                    category,
                    c.get("standardized_type", ""),
                    c.get("total_frequency", ""),
                    "; ".join(c.get("examples", [])),
                    "",  # est_hours - blank for Jason's team
                    "",  # material_cost - blank
                    "",  # sub_cost - blank
                    "",  # notes
                ])

            out.flush()
            time.sleep(1)

    print(f"\nDone! Results written to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
