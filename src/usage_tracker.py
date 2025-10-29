"""
Usage tracker with cost controls and budget management.
Tracks API calls, token usage, and enforces budget limits.
"""

import json
import os
import yaml
import fcntl
from datetime import datetime, date
from typing import Dict, Optional
from pathlib import Path
import logging
import threading

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when budget is exceeded and no fallback available."""
    pass


class UsageTracker:
    """
    Thread-safe AND process-safe usage tracker for API calls and budget enforcement.

    Uses:
    - threading.Lock() for thread safety within a process
    - fcntl file locking for process safety across multiple workers
    - Reload-before-write pattern to avoid race conditions
    """

    def __init__(self, config_path: str = "config/budget.yaml"):
        """
        Initialize usage tracker with thread-safe locking.

        Args:
            config_path: Path to budget configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.usage_file = Path(self.config['logging']['log_file'])
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)

        # Thread lock for concurrent access
        self._lock = threading.Lock()

        self.usage_data = self._load_usage()

    def _load_config(self) -> Dict:
        """Load budget configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config: {e}")
            raise

    def _load_usage(self) -> Dict:
        """Load usage data from JSON file with file locking (process-safe)."""
        if not self.usage_file.exists():
            return self._create_empty_usage()

        try:
            with open(self.usage_file, 'r') as f:
                # Acquire shared lock (multiple readers allowed)
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Reset if new month
            if data.get('month') != self._current_month():
                logger.info(f"New month detected. Resetting usage data.")
                return self._create_empty_usage()

            return data

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Invalid usage file: {e}. Creating new one.")
            return self._create_empty_usage()

    def _save_usage(self):
        """Save usage data to JSON file with file locking (process-safe)."""
        # Thread lock is held by caller (log_api_call)
        # File lock protects against other processes
        with open(self.usage_file, 'w') as f:
            # Acquire exclusive lock (only one writer)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(self.usage_data, f, indent=2, default=str)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _create_empty_usage(self) -> Dict:
        """Create empty usage data structure."""
        return {
            'month': self._current_month(),
            'total_cost': 0.0,
            'perplexity_cost': 0.0,  # Track Perplexity separately
            'total_calls': 0,
            'daily_usage': {},
            'model_usage': {},
            'provider_usage': {},  # Track by provider (perplexity, anthropic, google)
            'calls': []
        }

    @staticmethod
    def _current_month() -> str:
        """Get current month as YYYY-MM string."""
        return datetime.now().strftime('%Y-%m')

    @staticmethod
    def _current_date() -> str:
        """Get current date as YYYY-MM-DD string."""
        return datetime.now().strftime('%Y-%m-%d')

    def get_monthly_cost(self) -> float:
        """Get total cost for current month."""
        return self.usage_data['total_cost']

    def get_perplexity_cost(self) -> float:
        """Get Perplexity cost for current month."""
        return self.usage_data.get('perplexity_cost', 0.0)

    def get_remaining_budget(self) -> float:
        """Get remaining budget for current month."""
        monthly_budget = self.config['monthly_budget']
        return monthly_budget - self.usage_data['total_cost']

    def get_remaining_perplexity_budget(self) -> float:
        """Get remaining Perplexity budget for current month."""
        perplexity_limit = self.config.get('perplexity_monthly_limit', 4.98)
        return perplexity_limit - self.usage_data.get('perplexity_cost', 0.0)

    def _get_model_provider(self, model: str) -> str:
        """
        Get provider for a model.

        Returns:
            Provider name: 'perplexity', 'anthropic', or 'google'
        """
        if model in self.config['models']:
            model_config = self.config['models'][model]
            return model_config.get('provider', 'perplexity')  # Default to perplexity
        return 'perplexity'

    def get_available_model(self, preferred_model: str, use_case: str = "sentiment") -> tuple[str, str]:
        """
        Get best available model based on budget and cascade configuration.

        Args:
            preferred_model: Preferred model name (e.g., 'sonar-pro')
            use_case: Use case ('sentiment' or 'strategy')

        Returns:
            Tuple of (model_name, provider) or raises BudgetExceededError if no models available
        """
        cascade_enabled = self.config.get('model_cascade', {}).get('enabled', True)

        if not cascade_enabled:
            # Cascade disabled, just check if preferred model is available
            can_call, reason = self.can_make_call(preferred_model)
            if can_call:
                return preferred_model, self._get_model_provider(preferred_model)
            raise BudgetExceededError(reason)

        # Try preferred model first
        can_call, reason = self.can_make_call(preferred_model)
        if can_call:
            return preferred_model, self._get_model_provider(preferred_model)

        # Perplexity exhausted - check if we should fall back
        if "PERPLEXITY_LIMIT_EXCEEDED" in reason:
            logger.warning(f"⚠️  Perplexity limit reached ({reason})")
            logger.info("🔄 Attempting fallback to alternative models...")

            # Try fallback models in order
            cascade_order = self.config.get('model_cascade', {}).get('order', [])

            for provider in cascade_order:
                if provider == 'perplexity':
                    continue  # Already exhausted

                # Find a model from this provider
                fallback_model = self._get_fallback_model_for_provider(provider, use_case)

                if fallback_model:
                    can_call, fallback_reason = self.can_make_call(fallback_model)
                    if can_call:
                        logger.info(f"✓ Using fallback: {fallback_model} ({provider})")
                        return fallback_model, provider

            # No fallback models available
            raise BudgetExceededError("All models exhausted - Perplexity limit reached and no fallback models available")

        # Other budget issue
        raise BudgetExceededError(reason)

    def _get_fallback_model_for_provider(self, provider: str, use_case: str) -> Optional[str]:
        """Get fallback model name for a provider."""
        for model_name, model_config in self.config['models'].items():
            if model_config.get('provider') == provider:
                # Check if this model is suitable for the use case
                if model_config.get('use_case') in ['fallback', 'daily', use_case]:
                    return model_name
        return None

    def get_budget_percentage(self) -> float:
        """Get percentage of budget used."""
        monthly_budget = self.config['monthly_budget']
        if monthly_budget == 0:
            return 0.0
        return (self.usage_data['total_cost'] / monthly_budget) * 100

    def can_make_call(self, model: str, estimated_tokens: int = 1000) -> tuple[bool, str]:
        """
        Check if we can make an API call without exceeding budget (thread-safe).

        Args:
            model: Model name
            estimated_tokens: Estimated token count

        Returns:
            Tuple of (can_call, reason)
        """
        with self._lock:
            provider = self._get_model_provider(model)

            # Check Perplexity-specific hard limit ($4.98)
            if provider == 'perplexity':
                perplexity_limit = self.config.get('perplexity_monthly_limit', 4.98)
                perplexity_cost = self.usage_data.get('perplexity_cost', 0.0)

                if perplexity_cost >= perplexity_limit:
                    return False, f"PERPLEXITY_LIMIT_EXCEEDED: ${perplexity_cost:.2f} / ${perplexity_limit:.2f}"

            # Check monthly budget
            monthly_budget = self.config['monthly_budget']
            current_cost = self.usage_data['total_cost']

            if self.config.get('hard_stop', True) and current_cost >= monthly_budget:
                return False, f"Monthly budget exceeded (${current_cost:.2f} / ${monthly_budget:.2f})"

            # Estimate cost for this call
            if model in self.config['models']:
                cost_per_1k = self.config['models'][model]['cost_per_1k_tokens']
                estimated_cost = (estimated_tokens / 1000) * cost_per_1k

                # Check if adding this call would exceed Perplexity limit
                if provider == 'perplexity':
                    perplexity_cost = self.usage_data.get('perplexity_cost', 0.0)
                    perplexity_limit = self.config.get('perplexity_monthly_limit', 4.98)

                    if perplexity_cost + estimated_cost > perplexity_limit:
                        return False, f"PERPLEXITY_LIMIT_EXCEEDED: Would exceed (${perplexity_cost + estimated_cost:.2f} > ${perplexity_limit:.2f})"

                if current_cost + estimated_cost > monthly_budget:
                    return False, f"Would exceed budget (${current_cost + estimated_cost:.2f} > ${monthly_budget:.2f})"

            # Check daily limits
            today = self._current_date()
            daily_usage = self.usage_data['daily_usage'].get(today, {'calls': 0, 'model_calls': {}})

            max_calls = self.config['daily_limits']['max_api_calls']
            if daily_usage['calls'] >= max_calls:
                return False, f"DAILY_LIMIT: Daily API call limit reached ({daily_usage['calls']} / {max_calls}). Resets at midnight ET."

            # Check per-model daily limits
            model_call_key = f"{model}_calls"
            if model_call_key in self.config['daily_limits']:
                model_limit = self.config['daily_limits'][model_call_key]
                model_calls = daily_usage['model_calls'].get(model, 0)

                if model_calls >= model_limit:
                    return False, f"Daily limit for {model} reached ({model_calls} / {model_limit})"

            # Warn if approaching budget
            warn_percentage = self.config['warn_at_percentage']
            current_percentage = self.get_budget_percentage()

            if current_percentage >= warn_percentage:
                logger.warning(
                    f"⚠️  Budget warning: {current_percentage:.1f}% used "
                    f"(${current_cost:.2f} / ${monthly_budget:.2f})"
                )

            return True, "OK"

    def log_api_call(
        self,
        model: str,
        tokens_used: int,
        cost: float,
        ticker: Optional[str] = None,
        success: bool = True
    ):
        """
        Log an API call (thread-safe AND process-safe with atomic read-modify-write).

        Args:
            model: Model name
            tokens_used: Number of tokens used
            cost: Cost of the call
            ticker: Ticker symbol (if applicable)
            success: Whether call was successful
        """
        with self._lock:
            # Atomic read-modify-write: hold exclusive file lock for entire operation
            # This prevents race conditions where multiple processes read stale data
            with open(self.usage_file, 'r+') as f:
                # Acquire exclusive lock (blocks other readers and writers)
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Read current data
                    f.seek(0)
                    try:
                        self.usage_data = json.load(f)
                    except json.JSONDecodeError:
                        logger.warning("Invalid usage file, creating new one")
                        self.usage_data = self._create_empty_usage()

                    # Check if month reset needed
                    if self.usage_data.get('month') != self._current_month():
                        logger.info(f"New month detected. Resetting usage data.")
                        self.usage_data = self._create_empty_usage()

                    # Update in-memory data
                    today = self._current_date()
                    provider = self._get_model_provider(model)

                    # Update total cost
                    if success:
                        self.usage_data['total_cost'] += cost
                        self.usage_data['total_calls'] += 1

                        # Track Perplexity separately (for $4.98 hard limit)
                        if provider == 'perplexity':
                            if 'perplexity_cost' not in self.usage_data:
                                self.usage_data['perplexity_cost'] = 0.0
                            self.usage_data['perplexity_cost'] += cost

                        # Track by provider
                        if 'provider_usage' not in self.usage_data:
                            self.usage_data['provider_usage'] = {}
                        if provider not in self.usage_data['provider_usage']:
                            self.usage_data['provider_usage'][provider] = {'calls': 0, 'cost': 0.0}
                        self.usage_data['provider_usage'][provider]['calls'] += 1
                        self.usage_data['provider_usage'][provider]['cost'] += cost

                    # Update daily usage
                    if today not in self.usage_data['daily_usage']:
                        self.usage_data['daily_usage'][today] = {'calls': 0, 'cost': 0.0, 'model_calls': {}}

                    self.usage_data['daily_usage'][today]['calls'] += 1
                    self.usage_data['daily_usage'][today]['cost'] += cost

                    # Update per-model daily calls
                    if model not in self.usage_data['daily_usage'][today]['model_calls']:
                        self.usage_data['daily_usage'][today]['model_calls'][model] = 0
                    self.usage_data['daily_usage'][today]['model_calls'][model] += 1

                    # Update model usage
                    if model not in self.usage_data['model_usage']:
                        self.usage_data['model_usage'][model] = {'calls': 0, 'tokens': 0, 'cost': 0.0}

                    self.usage_data['model_usage'][model]['calls'] += 1
                    self.usage_data['model_usage'][model]['tokens'] += tokens_used
                    self.usage_data['model_usage'][model]['cost'] += cost

                    # Log call details
                    call_record = {
                        'timestamp': datetime.now().isoformat(),
                        'model': model,
                        'tokens': tokens_used,
                        'cost': cost,
                        'ticker': ticker,
                        'success': success
                    }
                    self.usage_data['calls'].append(call_record)

                    # Write back atomically (still holding exclusive lock)
                    f.seek(0)
                    f.truncate()
                    json.dump(self.usage_data, f, indent=2, default=str)

                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            if success:
                logger.info(
                    f"API call logged: {model} - {tokens_used} tokens - ${cost:.4f} "
                    f"(Total: ${self.usage_data['total_cost']:.2f})"
                )

    def get_dashboard_summary(self) -> Dict:
        """Get summary for dashboard display."""
        monthly_budget = self.config['monthly_budget']
        current_cost = self.usage_data['total_cost']
        remaining = self.get_remaining_budget()
        percentage = self.get_budget_percentage()

        today = self._current_date()
        today_usage = self.usage_data['daily_usage'].get(today, {'calls': 0, 'cost': 0.0})

        return {
            'month': self.usage_data['month'],
            'budget': {
                'total': monthly_budget,
                'used': current_cost,
                'remaining': remaining,
                'percentage': percentage
            },
            'today': {
                'calls': today_usage['calls'],
                'cost': today_usage.get('cost', 0.0)
            },
            'total_calls': self.usage_data['total_calls'],
            'model_usage': self.usage_data['model_usage']
        }


# CLI for dashboard
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    tracker = UsageTracker()
    summary = tracker.get_dashboard_summary()

    logger.info("")
    logger.info('='*60)
    logger.info('API USAGE DASHBOARD')
    logger.info('='*60)
    logger.info("")
    logger.info(f"Month: {summary['month']}")
    logger.info("")
    logger.info('Budget:')
    logger.info(f"  Total: ${summary['budget']['total']:.2f}")
    logger.info(f"  Used:  ${summary['budget']['used']:.2f} ({summary['budget']['percentage']:.1f}%)")
    logger.info(f"  Remaining: ${summary['budget']['remaining']:.2f}")
    logger.info("")
    logger.info('Today:')
    logger.info(f"  API Calls: {summary['today']['calls']}")
    logger.info(f"  Cost: ${summary['today']['cost']:.4f}")
    logger.info("")
    logger.info(f"Total Calls This Month: {summary['total_calls']}")
    logger.info("")
    if summary['model_usage']:
        logger.info('Model Usage:')
        for model, usage in summary['model_usage'].items():
            logger.info(f"  {model}:")
            logger.info(f"    Calls: {usage['calls']}")
            logger.info(f"    Tokens: {usage['tokens']:,}")
            logger.info(f"    Cost: ${usage['cost']:.4f}")
    logger.info("")
    logger.info('='*60)
