#!/usr/bin/env python3
"""Debug script to extract and examine PDF text format."""

import pdfplumber
from pathlib import Path

pdf_path = Path("../docs/2025 Trades/Statement1312025.pdf")

with pdfplumber.open(pdf_path) as pdf:
    # Extract text from pages 6-10 (where trades are)
    for page_num in [6, 7, 8]:
        print(f"\n{'=' * 80}")
        print(f"PAGE {page_num}")
        print("=" * 80)
        text = pdf.pages[page_num - 1].extract_text()
        print(text[:2000])  # First 2000 chars
