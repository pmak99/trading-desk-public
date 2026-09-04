-- Migration: Add directional bias prediction tracking
-- Version: 007
-- Description: Create tables to track bias predictions and validate against actual outcomes

-- Table to store directional bias predictions before earnings
CREATE TABLE IF NOT EXISTS bias_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    expiration DATE NOT NULL,

    -- Prediction data (captured before earnings)
    stock_price REAL NOT NULL,
    predicted_at DATETIME NOT NULL,

    -- Skew analysis results
    skew_atm REAL NOT NULL,           -- Skew at ATM (%)
    skew_curvature REAL NOT NULL,     -- Second derivative (smile vs smirk)
    skew_strength TEXT NOT NULL,      -- smile, smirk, inverse_smile
    slope_atm REAL NOT NULL,          -- First derivative at ATM

    -- Directional bias prediction
    directional_bias TEXT NOT NULL,   -- STRONG_BEARISH, BEARISH, WEAK_BEARISH, NEUTRAL, etc.
    bias_strength INTEGER NOT NULL,   -- 0=NEUTRAL, 1=WEAK, 2=MODERATE, 3=STRONG
    bias_confidence REAL NOT NULL,    -- 0.0-1.0 confidence in prediction
    r_squared REAL NOT NULL,          -- Fit quality
    num_points INTEGER NOT NULL,      -- Data points used in fit

    -- VRP context
    vrp_ratio REAL,
    implied_move_pct REAL,
    historical_mean_pct REAL,

    -- Outcome validation (filled after earnings)
    actual_move_pct REAL,             -- Actual close-to-close move
    actual_gap_pct REAL,              -- Actual gap move
    actual_direction TEXT,            -- UP, DOWN, FLAT
    prediction_correct BOOLEAN,       -- Did bias match direction?
    validated_at DATETIME,

    UNIQUE(ticker, earnings_date)
);

CREATE INDEX IF NOT EXISTS idx_bias_ticker ON bias_predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_bias_date ON bias_predictions(earnings_date);
CREATE INDEX IF NOT EXISTS idx_bias_strength ON bias_predictions(bias_strength);
CREATE INDEX IF NOT EXISTS idx_bias_validated ON bias_predictions(validated_at);

-- Table to store historical option chain snapshots (for future analysis)
CREATE TABLE IF NOT EXISTS option_chain_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    expiration DATE NOT NULL,
    captured_at DATETIME NOT NULL,

    -- Chain metadata
    stock_price REAL NOT NULL,
    dte INTEGER NOT NULL,  -- Days to expiration

    -- Strike-level data (JSON blob for flexibility)
    chain_data TEXT NOT NULL,  -- JSON: [{strike, call_iv, put_iv, call_delta, put_delta, ...}]

    UNIQUE(ticker, earnings_date, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_chain_ticker ON option_chain_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_chain_date ON option_chain_snapshots(earnings_date);

-- Summary statistics for bias prediction accuracy
CREATE TABLE IF NOT EXISTS bias_accuracy_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calculated_at DATETIME NOT NULL,

    -- Overall stats
    total_predictions INTEGER NOT NULL,
    total_validated INTEGER NOT NULL,

    -- By strength level
    strong_predictions INTEGER NOT NULL,
    strong_correct INTEGER NOT NULL,
    strong_accuracy REAL NOT NULL,

    moderate_predictions INTEGER NOT NULL,
    moderate_correct INTEGER NOT NULL,
    moderate_accuracy REAL NOT NULL,

    weak_predictions INTEGER NOT NULL,
    weak_correct INTEGER NOT NULL,
    weak_accuracy REAL NOT NULL,

    neutral_predictions INTEGER NOT NULL,
    neutral_correct INTEGER NOT NULL,
    neutral_accuracy REAL NOT NULL,

    -- By confidence bucket
    high_confidence_predictions INTEGER NOT NULL,    -- confidence > 0.7
    high_confidence_correct INTEGER NOT NULL,
    high_confidence_accuracy REAL NOT NULL,

    med_confidence_predictions INTEGER NOT NULL,     -- 0.3 < confidence <= 0.7
    med_confidence_correct INTEGER NOT NULL,
    med_confidence_accuracy REAL NOT NULL,

    low_confidence_predictions INTEGER NOT NULL,     -- confidence <= 0.3
    low_confidence_correct INTEGER NOT NULL,
    low_confidence_accuracy REAL NOT NULL
);
