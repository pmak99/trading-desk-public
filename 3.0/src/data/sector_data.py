"""
Sector and Industry data fetching for ML features.

Uses Yahoo Finance to fetch sector information and sector-level volatility metrics.
"""

import logging
from typing import Dict, Optional, Tuple
from datetime import date, timedelta
from functools import lru_cache

import yfinance as yf
import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    'SectorInfo',
    'get_sector_info',
    'get_sector_volatility',
    'SECTOR_ETFS',
]

# Sector ETF mappings for sector-level volatility
SECTOR_ETFS = {
    'Technology': 'XLK',
    'Healthcare': 'XLV',
    'Financial Services': 'XLF',
    'Consumer Cyclical': 'XLY',
    'Consumer Defensive': 'XLP',
    'Industrials': 'XLI',
    'Energy': 'XLE',
    'Utilities': 'XLU',
    'Real Estate': 'XLRE',
    'Basic Materials': 'XLB',
    'Communication Services': 'XLC',
}

# Industry group mappings (simplified)
INDUSTRY_GROUPS = {
    'Software': 'tech_software',
    'Semiconductors': 'tech_semis',
    'Internet Content & Information': 'tech_internet',
    'Consumer Electronics': 'tech_hardware',
    'Banks': 'fin_banks',
    'Insurance': 'fin_insurance',
    'Asset Management': 'fin_asset_mgmt',
    'Biotechnology': 'health_biotech',
    'Drug Manufacturers': 'health_pharma',
    'Medical Devices': 'health_devices',
    'Retail': 'consumer_retail',
    'Restaurants': 'consumer_restaurants',
    'Auto Manufacturers': 'consumer_auto',
    'Oil & Gas': 'energy_oil',
    'Utilities': 'utilities',
}


class SectorInfo:
    """Container for sector/industry information."""

    def __init__(
        self,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        industry_group: Optional[str] = None,
        sector_etf: Optional[str] = None,
    ):
        self.sector = sector
        self.industry = industry
        self.industry_group = industry_group
        self.sector_etf = sector_etf

    def to_features(self) -> Dict[str, float]:
        """Convert to ML feature dict with one-hot encoding."""
        features = {}

        # One-hot encode sector
        for sector_name in SECTOR_ETFS.keys():
            key = f"sector_{sector_name.lower().replace(' ', '_')}"
            features[key] = 1.0 if self.sector == sector_name else 0.0

        # Industry group encoding
        for group_name in set(INDUSTRY_GROUPS.values()):
            key = f"industry_{group_name}"
            features[key] = 1.0 if self.industry_group == group_name else 0.0

        return features


@lru_cache(maxsize=500)
def get_sector_info(ticker: str) -> SectorInfo:
    """
    Get sector and industry information for a ticker.

    Uses caching to avoid repeated API calls.

    Args:
        ticker: Stock symbol

    Returns:
        SectorInfo with sector/industry data
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        sector = info.get('sector')
        industry = info.get('industry')

        # Map to industry group
        industry_group = None
        if industry:
            for ind_name, group in INDUSTRY_GROUPS.items():
                if ind_name.lower() in industry.lower():
                    industry_group = group
                    break

        # Get sector ETF
        sector_etf = SECTOR_ETFS.get(sector) if sector else None

        return SectorInfo(
            sector=sector,
            industry=industry,
            industry_group=industry_group,
            sector_etf=sector_etf,
        )

    except Exception as e:
        logger.warning(f"Failed to get sector info for {ticker}: {e}")
        return SectorInfo()


def get_sector_volatility(
    sector_etf: str,
    as_of_date: date,
    lookback_days: int = 20
) -> Optional[Dict[str, float]]:
    """
    Get volatility metrics for a sector ETF.

    Args:
        sector_etf: Sector ETF symbol (e.g., 'XLK')
        as_of_date: Reference date
        lookback_days: Days of history to analyze

    Returns:
        Dict with sector volatility metrics
    """
    try:
        etf = yf.Ticker(sector_etf)
        start = as_of_date - timedelta(days=lookback_days + 10)
        hist = etf.history(start=start.isoformat(), end=as_of_date.isoformat())

        if len(hist) < lookback_days:
            return None

        # Calculate returns
        returns = hist['Close'].pct_change().dropna()

        # Volatility metrics
        hv = returns.std() * np.sqrt(252) * 100  # Annualized HV as percentage

        return {
            'sector_hv_20d': hv,
            'sector_return_20d': (hist['Close'].iloc[-1] / hist['Close'].iloc[0] - 1) * 100,
            'sector_avg_volume': hist['Volume'].mean(),
        }

    except Exception as e:
        logger.warning(f"Failed to get sector volatility for {sector_etf}: {e}")
        return None


def get_sector_features(ticker: str, as_of_date: date) -> Dict[str, float]:
    """
    Get all sector-related features for a ticker.

    Combines sector info with sector volatility metrics.

    Args:
        ticker: Stock symbol
        as_of_date: Reference date

    Returns:
        Dict of sector features for ML model
    """
    features = {}

    # Get sector info
    sector_info = get_sector_info(ticker)
    features.update(sector_info.to_features())

    # Get sector volatility if ETF is known
    if sector_info.sector_etf:
        vol_features = get_sector_volatility(sector_info.sector_etf, as_of_date)
        if vol_features:
            features.update(vol_features)

    return features
