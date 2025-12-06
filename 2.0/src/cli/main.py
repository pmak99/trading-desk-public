"""
IV Crush CLI - Main entry point.

Typer-based command-line interface for the IV Crush trading system.
Provides commands for scanning earnings, analyzing tickers, and system health.

Usage:
    ivcrush scan --date 2025-01-31
    ivcrush analyze AAPL MSFT GOOGL
    ivcrush health
    ivcrush sync-earnings

Installation:
    pip install -e .
    # Then use: ivcrush --help
"""

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.container import Container
from src.config.config import Config
from src.utils.logging import setup_logging

# Initialize Typer app
app = typer.Typer(
    name="ivcrush",
    help="IV Crush Trading System - Earnings options strategy scanner",
    add_completion=False,
    no_args_is_help=True,
)

# Rich console for styled output
console = Console()

# Logger
logger = logging.getLogger(__name__)


def _init_container() -> Container:
    """Initialize and return the DI container."""
    config = Config.from_env()
    return Container(config)


@app.command()
def scan(
    scan_date: str = typer.Option(
        None,
        "--date", "-d",
        help="Date to scan for earnings (YYYY-MM-DD). Defaults to tomorrow.",
    ),
    min_vrp: float = typer.Option(
        1.5,
        "--min-vrp",
        help="Minimum VRP ratio to display.",
    ),
    limit: int = typer.Option(
        20,
        "--limit", "-n",
        help="Maximum number of results to display.",
    ),
    strategies: bool = typer.Option(
        False,
        "--strategies", "-s",
        help="Generate strategy recommendations for tradeable tickers.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Enable verbose output.",
    ),
    json_logs: bool = typer.Option(
        False,
        "--json-logs",
        help="Output logs in JSON format for log aggregation.",
    ),
):
    """
    Scan for IV Crush opportunities on a specific date.

    Scans earnings announcements and calculates VRP ratios to identify
    trading opportunities with elevated implied volatility.
    """
    setup_logging(level="DEBUG" if verbose else "INFO", json_format=json_logs)

    # Parse date
    if scan_date:
        try:
            target_date = datetime.strptime(scan_date, "%Y-%m-%d").date()
        except ValueError:
            console.print(f"[red]Invalid date format: {scan_date}. Use YYYY-MM-DD.[/red]")
            raise typer.Exit(1)
    else:
        target_date = date.today() + timedelta(days=1)

    console.print(Panel(
        f"Scanning earnings for [bold cyan]{target_date}[/bold cyan]",
        title="IV Crush Scanner",
    ))

    try:
        container = _init_container()

        # Get earnings for date
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching earnings calendar...", total=None)

            earnings_result = container.earnings_repository.get_earnings_by_date(target_date)

            if earnings_result.is_err:
                console.print(f"[red]Error fetching earnings: {earnings_result.error}[/red]")
                raise typer.Exit(1)

            earnings = earnings_result.value
            progress.update(task, description=f"Found {len(earnings)} earnings announcements")

        if not earnings:
            console.print(f"[yellow]No earnings found for {target_date}[/yellow]")
            raise typer.Exit(0)

        # Analyze each ticker
        results = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing tickers...", total=len(earnings))

            for earning in earnings:
                ticker = earning.ticker
                progress.update(task, description=f"Analyzing {ticker}...")

                # Calculate expiration (typically day after earnings for weeklies)
                expiration = target_date + timedelta(days=1)

                analysis_result = container.ticker_analyzer.analyze(
                    ticker=ticker,
                    earnings_date=target_date,
                    expiration=expiration,
                    generate_strategies=strategies,
                )

                if analysis_result.is_ok:
                    analysis = analysis_result.value
                    if analysis.vrp.vrp_ratio >= min_vrp:
                        results.append(analysis)

                progress.advance(task)

        # Sort by VRP ratio
        results.sort(key=lambda x: x.vrp.vrp_ratio, reverse=True)
        results = results[:limit]

        # Display results
        if not results:
            console.print(f"[yellow]No opportunities found with VRP >= {min_vrp}[/yellow]")
            raise typer.Exit(0)

        _display_scan_results(results, strategies)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def analyze(
    tickers: List[str] = typer.Argument(
        ...,
        help="Ticker symbols to analyze (space-separated).",
    ),
    expiration_offset: int = typer.Option(
        1,
        "--expiration-offset", "-e",
        help="Days after earnings for option expiration.",
    ),
    strategies: bool = typer.Option(
        True,
        "--strategies/--no-strategies", "-s/-S",
        help="Generate strategy recommendations.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Enable verbose output.",
    ),
):
    """
    Analyze specific tickers for IV Crush opportunities.

    Calculates VRP ratios and optionally generates trade strategies
    for the specified ticker symbols.
    """
    setup_logging(level="DEBUG" if verbose else "INFO")

    console.print(Panel(
        f"Analyzing [bold cyan]{', '.join(tickers)}[/bold cyan]",
        title="IV Crush Analyzer",
    ))

    try:
        container = _init_container()

        results = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing tickers...", total=len(tickers))

            for ticker in tickers:
                ticker = ticker.upper().strip()
                progress.update(task, description=f"Analyzing {ticker}...")

                # Get earnings date from database or use tomorrow
                earnings_date = date.today() + timedelta(days=1)
                earnings_result = container.earnings_repository.get_next_earnings(ticker)
                if earnings_result.is_ok and earnings_result.value:
                    earnings_date = earnings_result.value.earnings_date

                expiration = earnings_date + timedelta(days=expiration_offset)

                analysis_result = container.ticker_analyzer.analyze(
                    ticker=ticker,
                    earnings_date=earnings_date,
                    expiration=expiration,
                    generate_strategies=strategies,
                )

                if analysis_result.is_ok:
                    results.append(analysis_result.value)
                else:
                    console.print(f"[yellow]{ticker}: {analysis_result.error.message}[/yellow]")

                progress.advance(task)

        if not results:
            console.print("[yellow]No valid analyses completed.[/yellow]")
            raise typer.Exit(1)

        _display_scan_results(results, strategies)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def health():
    """
    Check system health and dependencies.

    Verifies database connectivity, API availability, and configuration.
    """
    setup_logging(level="WARNING")

    console.print(Panel("System Health Check", title="IV Crush"))

    checks = []

    # Check config
    try:
        config = Config.from_env()
        checks.append(("Configuration", True, "Loaded from environment"))
    except Exception as e:
        checks.append(("Configuration", False, str(e)))

    # Check database
    try:
        container = Container(config)
        # Simple database check
        result = container.prices_repository.get_historical_moves("AAPL", limit=1)
        if result.is_ok:
            checks.append(("Database", True, "Connected"))
        else:
            checks.append(("Database", False, result.error.message))
    except Exception as e:
        checks.append(("Database", False, str(e)))

    # Check Tradier API
    try:
        price_result = container.tradier.get_stock_price("SPY")
        if price_result.is_ok:
            checks.append(("Tradier API", True, f"SPY @ ${float(price_result.value.amount):.2f}"))
        else:
            checks.append(("Tradier API", False, price_result.error.message))
    except Exception as e:
        checks.append(("Tradier API", False, str(e)))

    # Check VIX (market conditions)
    try:
        conditions_result = container.market_conditions_analyzer.get_current_conditions()
        if conditions_result.is_ok:
            conditions = conditions_result.value
            checks.append(("Market Conditions", True, f"VIX {conditions.vix_level.value:.1f} ({conditions.regime})"))
        else:
            checks.append(("Market Conditions", False, conditions_result.error.message))
    except Exception as e:
        checks.append(("Market Conditions", False, str(e)))

    # Display results
    table = Table(title="Health Checks")
    table.add_column("Component", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Details", style="dim")

    all_passed = True
    for component, passed, details in checks:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        if not passed:
            all_passed = False
        table.add_row(component, status, details)

    console.print(table)

    if all_passed:
        console.print("\n[green]All health checks passed![/green]")
    else:
        console.print("\n[red]Some health checks failed. See details above.[/red]")
        raise typer.Exit(1)


@app.command("sync-earnings")
def sync_earnings(
    days: int = typer.Option(
        14,
        "--days", "-d",
        help="Number of days ahead to sync.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Enable verbose output.",
    ),
):
    """
    Sync earnings calendar from external sources.

    Fetches upcoming earnings announcements and stores them in the database.
    """
    setup_logging(level="DEBUG" if verbose else "INFO")

    console.print(Panel(
        f"Syncing earnings for next [bold cyan]{days}[/bold cyan] days",
        title="Earnings Sync",
    ))

    try:
        container = _init_container()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Syncing earnings calendar...", total=None)

            # Use Alpha Vantage earnings calendar
            from src.infrastructure.api.alpha_vantage import AlphaVantageClient

            av_client = AlphaVantageClient(config=container.config)

            end_date = date.today() + timedelta(days=days)
            result = av_client.get_earnings_calendar(horizon="3month")

            if result.is_err:
                console.print(f"[red]Error: {result.error.message}[/red]")
                raise typer.Exit(1)

            earnings = result.value
            synced_count = 0

            for earning in earnings:
                if earning.earnings_date <= end_date:
                    save_result = container.earnings_repository.save_earnings(earning)
                    if save_result.is_ok:
                        synced_count += 1

            progress.update(task, description=f"Synced {synced_count} earnings")

        console.print(f"[green]Successfully synced {synced_count} earnings announcements.[/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def version():
    """Display version information."""
    console.print(Panel(
        "[bold]IV Crush Trading System[/bold]\n"
        "Version: 2.5.0\n"
        "Architecture: Clean Architecture with DI Container\n"
        "Features: VRP Analysis, Strategy Generation, Adaptive Thresholds",
        title="About",
    ))


def _display_scan_results(results: list, show_strategies: bool):
    """Display scan results in a formatted table."""
    table = Table(title="IV Crush Opportunities")

    table.add_column("Ticker", style="cyan", justify="left")
    table.add_column("VRP", justify="right")
    table.add_column("Implied", justify="right")
    table.add_column("Historical", justify="right")
    table.add_column("Rec", justify="center")

    if show_strategies:
        table.add_column("Strategy", justify="left")
        table.add_column("POP", justify="right")

    for analysis in results:
        vrp = analysis.vrp

        # Color code VRP
        vrp_str = f"{vrp.vrp_ratio:.2f}x"
        if vrp.vrp_ratio >= 7.0:
            vrp_str = f"[green]{vrp_str}[/green]"
        elif vrp.vrp_ratio >= 4.0:
            vrp_str = f"[yellow]{vrp_str}[/yellow]"

        # Recommendation color
        rec = vrp.recommendation.value
        if rec == "excellent":
            rec = f"[green]{rec.upper()}[/green]"
        elif rec == "good":
            rec = f"[yellow]{rec.upper()}[/yellow]"
        elif rec == "marginal":
            rec = f"[dim]{rec.upper()}[/dim]"
        else:
            rec = f"[red]{rec.upper()}[/red]"

        row = [
            analysis.ticker,
            vrp_str,
            f"{vrp.implied_move_pct.value:.1f}%",
            f"{vrp.historical_mean_move_pct.value:.1f}%",
            rec,
        ]

        if show_strategies and analysis.strategies:
            best = analysis.strategies.strategies[0]
            row.append(best.strategy_type.value)
            row.append(f"{best.probability_of_profit:.0%}")

        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]Found {len(results)} opportunities[/dim]")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
