# Logging standards

Conventions for `logger` calls across the codebase. Short by design — these are the rules
that have actually caused problems when broken.

## Format

Prefix ticker-scoped lines with the ticker, and include the values that made the decision:

```python
logger.info(f"{ticker}: VRP {vrp:.2f} ({recommendation}) — implied {implied:.1f}% vs mean {mean:.1f}%")
logger.warning(f"{ticker}: liquidity REJECT (OI={oi}, spread={spread:.1f}%) — skipping")
logger.error(f"{ticker}: chain fetch failed: {e}", exc_info=True)
```

A log line that says something failed without saying *what the inputs were* costs a
reproduction run to make useful.

## Rules

**Always pass `exc_info=True` on `logger.error` in an `except` block.** Without it the stack
trace is gone and you are debugging from one line of message text.

**Emojis in CLI output, never in logs.** `print()` is user-facing; `logger` output gets
grepped, piped through `journalctl`, and read by the Telegram failure reporter.

**Never log a secret, and never log a value where a key name would do.** The
`backup-secrets.sh` plaintext check greps the ciphertext for `TRADIER_API_KEY` — the *name* —
for exactly this reason.

**A cache hit is not worth a line.** Log the aggregate instead:

```python
# not this, once per ticker
logger.debug(f"Cache hit for {ticker}")

# this, once per run
logger.info(f"Cache hit rate: {hits/total:.1%} ({hits}/{total})")
```

**Levels:**

| | |
|---|---|
| `DEBUG` | per-item detail. Off in production; assume nobody reads it. |
| `INFO` | one line per meaningful step — what ran, on what, with what result. |
| `WARNING` | degraded but continuing. Say what the fallback was. |
| `ERROR` | the operation failed. Always with `exc_info=True`. |

**Say what happens next.** "retrying in 2s", "falling back to yfinance", "skipping ticker" —
a warning that does not say the consequence forces the reader to go find out.

## Scheduled jobs

Jobs on the server log to the journal and report failures to Telegram through
`scripts/jobs/runner.py`. Two consequences:

- An exception that escapes the job **is** the notification, so let it propagate rather than
  swallowing it into a `logger.error` and returning cleanly. A job that logs an error and
  exits 0 reports success.
- Conversely, a `logger.warning` reaches nobody in real time. If a condition should wake
  someone up, it needs to fail or ping the Kuma push monitor, not just log.
