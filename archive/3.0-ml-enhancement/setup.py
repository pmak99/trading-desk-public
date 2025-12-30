"""Setup script for 3.0 ML Trading System."""

from setuptools import setup, find_packages

setup(
    name="ivcrush-ml",
    version="3.0.0",
    description="ML-Enhanced IV Crush Earnings Trading System",
    author="Prashant Makwana",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "scikit-learn>=1.3.0",
        "scipy>=1.11.0",
        "ta>=0.11.0",
        "statsmodels>=0.14.0",
        "matplotlib>=3.7.0",
        "seaborn>=0.12.0",
        "plotly>=5.14.0",
        "python-dotenv>=1.0.0",
        "pyyaml>=6.0",
        "tqdm>=4.65.0",
        "joblib>=1.3.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "jupyter>=1.0.0",
            "ipykernel>=6.25.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "ml-collect=scripts.collect_data:main",
            "ml-features=scripts.generate_features:main",
            "ml-train=scripts.train_models:main",
            "ml-evaluate=scripts.evaluate_models:main",
        ],
    },
)
