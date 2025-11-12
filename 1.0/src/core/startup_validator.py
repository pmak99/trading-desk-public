"""
Startup validation for environment, API keys, and configuration.

Validates critical dependencies at startup to fail fast with clear error messages
rather than failing mid-execution with confusing errors.
"""

import os
import logging
from typing import List, Dict, Optional
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)


class StartupValidator:
    """Validate environment and dependencies at startup."""

    @staticmethod
    def validate_required_apis(mode: str = 'full') -> List[str]:
        """
        Validate required API keys are present.

        Args:
            mode: 'full' = all APIs recommended, 'minimal' = only critical APIs

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []

        # Critical API keys
        if not os.getenv('TRADIER_ACCESS_TOKEN'):
            errors.append("❌ TRADIER_ACCESS_TOKEN not set - required for real IV data")
            errors.append("   Get token at: https://dash.tradier.com/settings/api")
            errors.append("   Without this, analysis will use less accurate yfinance data")

        if mode == 'full':
            # Recommended but optional
            if not os.getenv('PERPLEXITY_API_KEY') and not os.getenv('GOOGLE_API_KEY'):
                logger.warning("⚠️  No AI API keys set (PERPLEXITY_API_KEY or GOOGLE_API_KEY)")
                logger.warning("   AI-powered sentiment and strategy generation will be disabled")
                logger.warning("   Get Perplexity key at: https://www.perplexity.ai/api")

            if not os.getenv('ALPHA_VANTAGE_API_KEY'):
                logger.warning("⚠️  ALPHA_VANTAGE_API_KEY not set - will use Nasdaq calendar")
                logger.warning("   Alpha Vantage provides more reliable earnings dates")
                logger.warning("   Get free key at: https://www.alphavantage.co/support/#api-key")

        return errors

    @staticmethod
    def validate_config_files() -> List[str]:
        """
        Validate configuration files exist and have valid structure.

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []
        # Config files are in project root/config, not src/config
        config_dir = Path(__file__).parent.parent.parent / 'config'

        # Check budget.yaml
        budget_file = config_dir / 'budget.yaml'
        if not budget_file.exists():
            errors.append(f"❌ Config file not found: {budget_file}")
        else:
            try:
                with open(budget_file) as f:
                    config = yaml.safe_load(f)

                if not config:
                    errors.append(f"❌ {budget_file.name} is empty or invalid YAML")
                else:
                    # Validate required fields
                    required = ['monthly_budget', 'perplexity_monthly_limit']
                    for field in required:
                        if field not in config:
                            errors.append(f"❌ {budget_file.name}: Missing required field '{field}'")

                    # Validate numeric ranges
                    if config.get('monthly_budget', 0) <= 0:
                        errors.append(f"❌ {budget_file.name}: monthly_budget must be > 0")

                    # Validate model definitions
                    if 'models' in config:
                        for model_name, model_config in config['models'].items():
                            if not isinstance(model_config, dict):
                                errors.append(f"❌ {budget_file.name}: Model '{model_name}' config must be a dictionary")
                            else:
                                # Check for new pricing structure
                                required_fields = ['input_cost_per_1k', 'output_cost_per_1k', 'per_request_fee']
                                missing_fields = [f for f in required_fields if f not in model_config]
                                if missing_fields:
                                    errors.append(
                                        f"❌ {budget_file.name}: Model '{model_name}' missing required pricing fields: "
                                        f"{', '.join(missing_fields)}"
                                    )

            except yaml.YAMLError as e:
                errors.append(f"❌ {budget_file.name}: Invalid YAML syntax")
                errors.append(f"   {str(e)}")
            except Exception as e:
                errors.append(f"❌ {budget_file.name}: Error reading file: {e}")

        # Check trading_criteria.yaml
        criteria_file = config_dir / 'trading_criteria.yaml'
        if not criteria_file.exists():
            errors.append(f"❌ Config file not found: {criteria_file}")
        else:
            try:
                with open(criteria_file) as f:
                    config = yaml.safe_load(f)

                if not config:
                    errors.append(f"❌ {criteria_file.name} is empty or invalid YAML")
                else:
                    # Validate required sections
                    required_sections = ['iv_thresholds', 'iv_rank_thresholds', 'scoring_weights']
                    for section in required_sections:
                        if section not in config:
                            errors.append(f"❌ {criteria_file.name}: Missing required section '{section}'")

                    # Validate IV thresholds
                    if 'iv_thresholds' in config:
                        iv_config = config['iv_thresholds']
                        if 'minimum' not in iv_config or 'excellent' not in iv_config:
                            errors.append(f"❌ {criteria_file.name}: iv_thresholds must have 'minimum' and 'excellent'")
                        elif iv_config.get('minimum', 0) >= iv_config.get('excellent', 100):
                            errors.append(f"❌ {criteria_file.name}: iv_thresholds.minimum must be < excellent")

                    # Note: Scoring weights validation removed - weights are applied individually,
                    # not normalized, so they don't need to sum to 1.0

            except yaml.YAMLError as e:
                errors.append(f"❌ {criteria_file.name}: Invalid YAML syntax")
                errors.append(f"   {str(e)}")
            except Exception as e:
                errors.append(f"❌ {criteria_file.name}: Error reading file: {e}")

        return errors

    @staticmethod
    def validate_environment() -> List[str]:
        """
        Validate Python environment and dependencies.

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []

        # Check critical dependencies
        try:
            import yfinance
        except ImportError:
            errors.append("❌ yfinance not installed")
            errors.append("   Install: pip install yfinance")

        try:
            import requests
        except ImportError:
            errors.append("❌ requests not installed")
            errors.append("   Install: pip install requests")

        try:
            import yaml
        except ImportError:
            errors.append("❌ PyYAML not installed")
            errors.append("   Install: pip install pyyaml")

        return errors

    @staticmethod
    def validate_all(mode: str = 'full') -> Dict[str, List[str]]:
        """
        Run all validations and return results.

        Args:
            mode: 'full' = all checks, 'minimal' = only critical checks

        Returns:
            Dict with validation results:
                {
                    'api_keys': [...errors...],
                    'config_files': [...errors...],
                    'environment': [...errors...]
                }
        """
        results = {
            'api_keys': StartupValidator.validate_required_apis(mode),
            'config_files': StartupValidator.validate_config_files(),
            'environment': StartupValidator.validate_environment()
        }

        return results

    @staticmethod
    def check_and_exit_on_errors(mode: str = 'full', strict: bool = True) -> bool:
        """
        Validate environment and exit if critical errors found.

        Args:
            mode: 'full' or 'minimal' validation
            strict: If True, exit on any errors. If False, only exit on critical errors.

        Returns:
            True if validation passed, False otherwise (only if strict=False)
        """
        results = StartupValidator.validate_all(mode)

        # Check for critical errors
        has_errors = False
        for category, errors in results.items():
            if errors:
                # Filter critical errors (those starting with ❌)
                critical_errors = [e for e in errors if e.startswith('❌')]

                if critical_errors:
                    has_errors = True
                    logger.error(f"\n{category.upper().replace('_', ' ')} VALIDATION FAILED:")
                    for error in errors:
                        if error.startswith('❌') or error.startswith('   '):
                            logger.error(error)
                        else:
                            logger.warning(error)

        if has_errors:
            if strict:
                logger.error("\n" + "="*70)
                logger.error("STARTUP VALIDATION FAILED")
                logger.error("="*70)
                logger.error("Fix the errors above before running the analyzer.")
                logger.error("See README.md for setup instructions.")
                return False
            else:
                logger.warning("\n⚠️  Some validation checks failed but continuing anyway...")
                return True
        else:
            logger.info("✅ Startup validation passed")
            return True


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    print("\n" + "="*70)
    print("STARTUP VALIDATION CHECK")
    print("="*70 + "\n")

    success = StartupValidator.check_and_exit_on_errors(mode='full', strict=False)

    print("\n" + "="*70)
    if success:
        print("✅ VALIDATION PASSED")
    else:
        print("❌ VALIDATION FAILED - See errors above")
    print("="*70 + "\n")
