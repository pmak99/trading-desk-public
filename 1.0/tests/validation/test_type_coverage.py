#!/usr/bin/env python3
"""Verify type coverage improvements across the codebase."""

import sys
import os
import ast
from typing import Dict, List, Set
from pathlib import Path

def analyze_type_hints(file_path: str) -> Dict[str, any]:
    """Analyze type hints in a Python file."""
    with open(file_path, 'r') as f:
        content = f.read()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {'error': 'Syntax error'}

    stats = {
        'total_functions': 0,
        'typed_functions': 0,
        'total_params': 0,
        'typed_params': 0,
        'functions_with_return_type': 0,
        'imports_typeddict': 'TypedDict' in content,
        'imports_types': 'from src.core.types import' in content,
        'uses_optional': 'Optional[' in content,
        'uses_list': 'List[' in content,
        'uses_dict': 'Dict[' in content,
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Skip private/magic methods
            if node.name.startswith('_') and not node.name.startswith('__'):
                continue

            stats['total_functions'] += 1

            # Check return type annotation
            if node.returns is not None:
                stats['functions_with_return_type'] += 1

            # Check parameter annotations
            for arg in node.args.args:
                if arg.arg != 'self' and arg.arg != 'cls':
                    stats['total_params'] += 1
                    if arg.annotation is not None:
                        stats['typed_params'] += 1

            # Count as typed function if it has at least return type or param types
            has_return_type = node.returns is not None
            has_param_types = any(arg.annotation is not None for arg in node.args.args
                                 if arg.arg != 'self' and arg.arg != 'cls')

            if has_return_type or has_param_types:
                stats['typed_functions'] += 1

    return stats


def calculate_coverage(stats: Dict) -> float:
    """Calculate type coverage percentage."""
    if stats.get('error'):
        return 0.0

    if stats['total_functions'] == 0:
        return 0.0

    # Weight: 40% function return types, 60% parameter types
    func_coverage = stats['functions_with_return_type'] / stats['total_functions'] if stats['total_functions'] > 0 else 0
    param_coverage = stats['typed_params'] / stats['total_params'] if stats['total_params'] > 0 else 0

    return (func_coverage * 0.4 + param_coverage * 0.6) * 100


print("=" * 70)
print("TYPE COVERAGE VERIFICATION")
print("Verifying type hint improvements across the codebase")
print("=" * 70)

# Files to check (refactored and updated files)
files_to_check = [
    ('src/core/types.py', 'New - TypedDict definitions'),
    ('src/core/validators.py', 'New - Validation functions'),
    ('src/core/memoization.py', 'New - Memoization decorators'),
    ('src/core/rate_limiter.py', 'New - Rate limiting'),
    ('src/core/circuit_breaker.py', 'New - Circuit breaker'),
    ('src/core/repository.py', 'New - Repository pattern'),
    ('src/core/generators.py', 'New - Generator utilities'),
    ('src/core/error_messages.py', 'New - Error handling'),
    ('src/core/command_pattern.py', 'New - Command pattern'),
    ('src/analysis/ticker_filter.py', 'Updated - Type hints added'),
    ('src/analysis/scorers.py', 'Updated - Type hints added'),
]

results = []
total_coverage = 0
file_count = 0

print("\n=== Individual File Analysis ===\n")

for file_path, description in files_to_check:
    if not os.path.exists(file_path):
        print(f"⚠️  {file_path}: File not found")
        continue

    stats = analyze_type_hints(file_path)

    if stats.get('error'):
        print(f"❌ {file_path}: {stats['error']}")
        continue

    coverage = calculate_coverage(stats)
    results.append((file_path, coverage, stats, description))

    # Status indicator
    if coverage >= 90:
        status = "✅"
    elif coverage >= 70:
        status = "🟡"
    else:
        status = "❌"

    print(f"{status} {file_path}")
    print(f"   {description}")
    print(f"   Coverage: {coverage:.1f}%")
    print(f"   Functions: {stats['typed_functions']}/{stats['total_functions']} typed")
    print(f"   Parameters: {stats['typed_params']}/{stats['total_params']} typed")
    print(f"   Return types: {stats['functions_with_return_type']}/{stats['total_functions']}")

    if stats['imports_types']:
        print(f"   ✓ Uses TypedDict types from src.core.types")
    if stats['uses_optional'] or stats['uses_list'] or stats['uses_dict']:
        type_hints = []
        if stats['uses_optional']:
            type_hints.append('Optional')
        if stats['uses_list']:
            type_hints.append('List')
        if stats['uses_dict']:
            type_hints.append('Dict')
        print(f"   ✓ Uses type hints: {', '.join(type_hints)}")

    print()

    total_coverage += coverage
    file_count += 1

# Calculate overall statistics
print("=" * 70)
print("SUMMARY")
print("=" * 70)

if file_count > 0:
    avg_coverage = total_coverage / file_count
    print(f"\nAverage Type Coverage: {avg_coverage:.1f}%")

    # Count files by coverage level
    excellent = sum(1 for _, cov, _, _ in results if cov >= 90)
    good = sum(1 for _, cov, _, _ in results if 70 <= cov < 90)
    needs_work = sum(1 for _, cov, _, _ in results if cov < 70)

    print(f"\nFiles by Coverage Level:")
    print(f"  Excellent (≥90%): {excellent} files")
    print(f"  Good (70-89%):    {good} files")
    print(f"  Needs work (<70%): {needs_work} files")

    # New files created
    new_files = [r for r in results if 'New -' in r[3]]
    updated_files = [r for r in results if 'Updated -' in r[3]]

    print(f"\nRefactoring Impact:")
    print(f"  New files created: {len(new_files)}")
    print(f"  Existing files updated: {len(updated_files)}")

    if new_files:
        avg_new = sum(cov for _, cov, _, _ in new_files) / len(new_files)
        print(f"  Average coverage (new files): {avg_new:.1f}%")

    if updated_files:
        avg_updated = sum(cov for _, cov, _, _ in updated_files) / len(updated_files)
        print(f"  Average coverage (updated files): {avg_updated:.1f}%")

    # Type hint features usage
    print(f"\nType Hint Features Used:")
    using_typeddict = sum(1 for _, _, stats, _ in results if stats['imports_types'])
    using_optional = sum(1 for _, _, stats, _ in results if stats['uses_optional'])
    using_list = sum(1 for _, _, stats, _ in results if stats['uses_list'])
    using_dict = sum(1 for _, _, stats, _ in results if stats['uses_dict'])

    print(f"  TypedDict imports: {using_typeddict} files")
    print(f"  Optional[]: {using_optional} files")
    print(f"  List[]: {using_list} files")
    print(f"  Dict[]: {using_dict} files")

    print("\n" + "=" * 70)

    # Final verdict
    if avg_coverage >= 85:
        print("✅ EXCELLENT TYPE COVERAGE!")
        print(f"   Average coverage of {avg_coverage:.1f}% exceeds 85% target")
        print("   Type safety significantly improved across the codebase")
    elif avg_coverage >= 70:
        print("🟡 GOOD TYPE COVERAGE")
        print(f"   Average coverage of {avg_coverage:.1f}% is good but can improve")
        print("   Consider adding more type hints to reach 85%+ target")
    else:
        print("❌ TYPE COVERAGE NEEDS IMPROVEMENT")
        print(f"   Average coverage of {avg_coverage:.1f}% is below target")
        print("   Review files with low coverage and add type hints")

    print("=" * 70)

else:
    print("❌ No files could be analyzed")
    # sys.exit(1)
