"""
Memory-efficient generators for batch processing.

Provides generator-based utilities for processing large datasets without
loading everything into memory at once.
"""

import logging
from typing import Iterator, List, TypeVar, Callable, Optional, Any

logger = logging.getLogger(__name__)

T = TypeVar('T')


def chunked(iterable: List[T], chunk_size: int) -> Iterator[List[T]]:
    """
    Yield successive chunks from an iterable.

    Memory-efficient alternative to processing entire lists at once.
    Particularly useful for API batch operations.

    Args:
        iterable: Input list
        chunk_size: Size of each chunk

    Yields:
        Lists of size chunk_size (last chunk may be smaller)

    Example:
        for chunk in chunked(tickers, 50):
            process_batch(chunk)  # Process 50 at a time
    """
    for i in range(0, len(iterable), chunk_size):
        yield iterable[i:i + chunk_size]


def filtered_generator(
    items: List[T],
    predicate: Callable[[T], bool]
) -> Iterator[T]:
    """
    Generator that yields items passing a filter predicate.

    Memory-efficient filtering - doesn't create intermediate lists.

    Args:
        items: Input items
        predicate: Function returning True for items to keep

    Yields:
        Items that pass the predicate

    Example:
        valid_tickers = filtered_generator(
            tickers,
            lambda t: t['score'] > 50
        )
    """
    for item in items:
        try:
            if predicate(item):
                yield item
        except Exception as e:
            logger.debug(f"Filter predicate failed for item: {e}")
            continue


def batch_process_generator(
    items: List[T],
    processor: Callable[[T], Optional[Any]],
    chunk_size: int = 50,
    log_progress: bool = True
) -> Iterator[Any]:
    """
    Process items in batches using a generator.

    Yields results as they're processed instead of waiting for all to complete.
    Useful for long-running operations where you want incremental results.

    Args:
        items: Items to process
        processor: Function to process each item
        chunk_size: Size of processing chunks
        log_progress: Whether to log progress

    Yields:
        Processed results (None results are skipped)

    Example:
        for result in batch_process_generator(tickers, fetch_data):
            save_result(result)  # Save incrementally
    """
    total = len(items)
    processed = 0

    for chunk in chunked(items, chunk_size):
        for item in chunk:
            try:
                result = processor(item)
                if result is not None:
                    yield result
                processed += 1
            except Exception as e:
                logger.error(f"Processing failed for item: {e}")
                processed += 1
                continue

        if log_progress:
            logger.info(f"Progress: {processed}/{total} items processed ({processed/total*100:.1f}%)")


def parallel_batch_generator(
    items: List[T],
    processor: Callable[[T], Optional[Any]],
    chunk_size: int = 50,
    max_workers: int = 4
) -> Iterator[Any]:
    """
    Process items in parallel batches using a generator.

    Combines chunking with parallel processing for optimal performance.
    Results are yielded as they complete, not in input order.

    Args:
        items: Items to process
        processor: Function to process each item
        chunk_size: Size of each chunk
        max_workers: Number of parallel workers

    Yields:
        Processed results as they complete

    Example:
        for ticker_data in parallel_batch_generator(tickers, fetch_ticker):
            analyze(ticker_data)  # Process as results arrive
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(items)
    completed = 0

    for chunk in chunked(items, chunk_size):
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit chunk for processing
            future_to_item = {
                executor.submit(processor, item): item
                for item in chunk
            }

            # Yield results as they complete
            for future in as_completed(future_to_item):
                try:
                    result = future.result()
                    if result is not None:
                        yield result
                    completed += 1
                except Exception as e:
                    item = future_to_item[future]
                    logger.error(f"Processing failed for {item}: {e}")
                    completed += 1

        logger.debug(f"Processed chunk: {completed}/{total} items complete")


def ticker_stream_generator(
    tickers: List[str],
    fetcher: Callable[[str], Optional[T]],
    filter_func: Optional[Callable[[T], bool]] = None,
    chunk_size: int = 50
) -> Iterator[T]:
    """
    Stream ticker data with filtering and chunking.

    Optimized generator specifically for ticker data processing.
    Fetches, filters, and yields results incrementally.

    Args:
        tickers: List of ticker symbols
        fetcher: Function to fetch ticker data
        filter_func: Optional filter function (e.g., score > threshold)
        chunk_size: Batch size for fetching

    Yields:
        Ticker data that passes filters

    Example:
        for ticker_data in ticker_stream_generator(
            tickers=['AAPL', 'MSFT', ...],
            fetcher=fetch_ticker_data,
            filter_func=lambda d: d['score'] > 70
        ):
            process_high_quality_ticker(ticker_data)
    """
    total_fetched = 0
    total_yielded = 0

    for chunk in chunked(tickers, chunk_size):
        logger.debug(f"Fetching chunk of {len(chunk)} tickers...")

        for ticker in chunk:
            try:
                data = fetcher(ticker)
                total_fetched += 1

                if data is None:
                    continue

                # Apply filter if provided
                if filter_func is None or filter_func(data):
                    total_yielded += 1
                    yield data

            except Exception as e:
                logger.warning(f"{ticker}: Fetch failed: {e}")
                total_fetched += 1
                continue

    logger.info(
        f"Stream complete: {total_yielded}/{total_fetched} tickers passed "
        f"filters ({total_yielded/max(total_fetched, 1)*100:.1f}%)"
    )


def lazy_map(
    func: Callable[[T], Any],
    items: List[T]
) -> Iterator[Any]:
    """
    Lazy map that yields results without creating intermediate list.

    Memory-efficient alternative to list comprehensions for large datasets.

    Args:
        func: Function to apply to each item
        items: Input items

    Yields:
        Transformed items

    Example:
        # Instead of: scores = [calculate_score(t) for t in tickers]
        # Use: scores = list(lazy_map(calculate_score, tickers))
        # Or process without materializing: for score in lazy_map(...):
    """
    for item in items:
        try:
            yield func(item)
        except Exception as e:
            logger.debug(f"Map function failed for item: {e}")
            continue


def sliding_window(
    items: List[T],
    window_size: int
) -> Iterator[List[T]]:
    """
    Generate sliding windows over a list.

    Useful for time-series analysis or moving averages.

    Args:
        items: Input items
        window_size: Size of sliding window

    Yields:
        Windows of size window_size

    Example:
        for window in sliding_window(prices, 5):
            moving_avg = sum(window) / len(window)
    """
    if len(items) < window_size:
        return

    for i in range(len(items) - window_size + 1):
        yield items[i:i + window_size]
