# 3.0 ML-Enhanced IV Crush Trading System

**Status:** Development
**Phase:** Phase 1 - Data Collection & Feature Engineering
**Baseline to Beat:** 57.4% win rate, 1.19 profit factor, $261k YTD

---

## Overview

The 3.0 system uses machine learning to predict earnings move magnitude, improving on the 2.0 system's historical mean approach. This enables:

- **Better strike selection** via predicted move magnitude
- **Optimized position sizing** based on prediction confidence
- **Dynamic strategy selection** (naked/spread/iron condor) per trade
- **Improved trade filtering** to focus on high-probability setups

## Quick Start

```bash
# Setup environment
cd 3.0
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run data collection
python scripts/collect_data.py

# Generate features
python scripts/generate_features.py

# Train models
python scripts/train_models.py

# Evaluate
python scripts/evaluate_models.py
```

## Architecture

### Data Pipeline
1. **Data Loading** (`src/data/loader.py`) - Pull from existing ivcrush.db
2. **Feature Engineering** (`src/features/`) - 5 feature categories
3. **Preprocessing** (`src/data/preprocessor.py`) - Scaling, encoding

### ML Models
- **Magnitude Predictor** - Predicts move size (regression)
- **Direction Classifier** - Up/down prediction (classification)
- **Volatility Predictor** - IV change prediction
- **Ensemble** - Combines all models for final decision

### Integration
- **Real-time Predictor** (`src/trading/predictor.py`) - Live predictions
- **Strategy Layer** (`src/trading/strategy.py`) - ML-enhanced trade logic
- **2.0 Integration** (`src/trading/integration.py`) - Backwards compatible

## Success Criteria

Must beat 2.0 baseline:
- **Minimum:** Win rate > 60.3%, Sharpe > 7.26
- **Target:** Win rate > 63.2%, Sharpe > 8.25

## Development Phases

- **Phase 1** (Current): Feature engineering
- **Phase 2**: Model training & validation
- **Phase 3**: Integration & deployment

See `PROGRESS.md` for detailed task tracking.

## Directory Structure

```
3.0/
├── config/          # Model and training configs
├── data/            # Raw, processed, features, predictions
├── models/          # Trained model artifacts
├── src/             # Source code (data, features, models, trading)
├── notebooks/       # Jupyter notebooks for exploration
├── tests/           # Unit and integration tests
├── scripts/         # Training and deployment scripts
└── reports/         # Experiment results and metrics
```

## Configuration

All configs are in `config/`:
- `model_config.json` - Model architecture & hyperparameters
- `feature_config.json` - Feature engineering settings
- `training_config.json` - Training parameters (epochs, batch size, etc.)

## Testing

```bash
pytest tests/
```

## Documentation

- [Feature Engineering Guide](docs/features.md)
- [Model Training Guide](docs/training.md)
- [Integration Guide](docs/integration.md)

## License

Proprietary
