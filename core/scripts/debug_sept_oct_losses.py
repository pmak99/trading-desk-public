#!/usr/bin/env python3
"""Debug Sept/Oct losses for NFLX, META, AVGO."""

import pdfplumber
from pathlib import Path

# Check September and October statements
for month in ["Statement9302025.pdf", "Statement10312025.pdf"]:
    pdf_path = Path(f"../docs/2025 Trades/{month}")

    print("\n" + "="*80)
    print(f"CHECKING {month}")
    print("="*80)

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in range(len(pdf.pages)):
            text = pdf.pages[page_num].extract_text()

            # Find lines with these symbols
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if any(sym in line for sym in ['NFLX', 'META', 'AVGO']):
                    # Print context (3 lines before and after)
                    start = max(0, i-3)
                    end = min(len(lines), i+4)
                    print("\n".join(lines[start:end]))
                    print("-" * 40)
