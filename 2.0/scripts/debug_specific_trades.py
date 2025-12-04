#!/usr/bin/env python3
"""Debug specific symbols to understand trade structure."""

import pdfplumber
from pathlib import Path
import re

# Check January statement for NFLX and META
pdf_path = Path("../docs/2025 Trades/Statement1312025.pdf")

print("="*80)
print("EXTRACTING NFLX, META, AVGO TRADES FROM JANUARY 2025")
print("="*80)

with pdfplumber.open(pdf_path) as pdf:
    for page_num in range(len(pdf.pages)):
        text = pdf.pages[page_num].extract_text()

        # Find lines with these symbols
        for line in text.split('\n'):
            if any(sym in line for sym in ['NFLX', 'META', 'AVGO', 'ASML', 'NVDA', 'GME', 'SPY']):
                print(line)
