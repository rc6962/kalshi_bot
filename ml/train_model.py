"""
LightGBM Training Script for Kalshi Mean-Reversion Strategy
Trains a binary classifier to predict trade profitability.
"""
import os
import sys
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

# Add parent directory to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Feature columns used for training (must match trade_logger output)
FEATURE_COLS = [
    "multiplier", "strike_distance_pct", "recent_move_pct",
    "time_remaining_sec", "futures_trend", "spread_pct"
]
TARGET_COL = "outcome"  # 1 = WIN/profitable, 0 = LOSS
MODEL_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "kalshi_lgbm_model.txt")


def load_training_data(csv_path="ml_training_data.csv"):
    """Load and validate training data from CSV."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Training data not found at {csv_path}. "
            "Run 'python backtest_ml_data.py' first to generate synthetic data, "
            "or accumulate live trades via trade_logger."
        )
    
    df = pd.read_csv(csv_path)
    
    # Auto-fill missing spread_pct if not in CSV for backward compatibility
    if "spread_pct" not in df.columns:
        print("[train_model] 'spread_pct' column missing in training data. Auto-filling with default 0.02.")
        df["spread_pct"] = 0.02
    
    # Validate required columns exist
    missing = [c for c in FEATURE_COLS + [TARGET_COL] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in training data: {missing}")
    
    # Drop rows with NaN in critical features
    df = df.dropna(subset=FEATURE_COLS + [TARGET_COL])
    
    print(f"Loaded {len(df)} samples from {csv_path}")
    print(f"Class distribution: {df[TARGET_COL].value_counts().to_dict()}")
    return df


def train_model(df, test_size=0.2, random_state=42):
    """Train LightGBM classifier with stratified split."""
    X = df[FEATURE_COLS]
    y = df[TARGET_COL].astype(int)
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )
    
    # LightGBM parameters tuned for small/tabular trading data
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "boosting_type": "gbdt",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_child_samples": 20,  # Prevent overfitting on small datasets
        "max_depth": 5,
        "verbose": -1,
        "n_estimators": 200,
        "random_state": random_state
    }
    
    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[lgb.log_evaluation(period=50)]
    )
    
    # Evaluate on test set
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("\n=== Model Evaluation ===")
    print(f"Accuracy:  {accuracy_score(y_test, y_pred):.4f}")
    print(f"Precision: {precision_score(y_test, y_pred, zero_division=0):.4f}")
    print(f"Recall:    {recall_score(y_test, y_pred, zero_division=0):.4f}")
    print(f"F1 Score:  {f1_score(y_test, y_pred, zero_division=0):.4f}")
    
    # Feature importance
    print("\n=== Feature Importance ===")
    importance = pd.Series(model.feature_importances_, index=FEATURE_COLS)
    print(importance.sort_values(ascending=False))
    
    return model


def save_model(model, path=MODEL_OUTPUT_PATH):
    """Save trained model using LightGBM native text format for fast loading."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    model.booster_.save_model(path)
    print(f"\nModel saved to {path} (native LightGBM format)")


def main():
    # Support both synthetic backtest data and live trade logs
    import argparse
    parser = argparse.ArgumentParser(description="Train LightGBM model for Kalshi bot")
    parser.add_argument("--data", default="ml_training_data.csv", help="Path to training CSV")
    args = parser.parse_args()
    
    print("Loading training data...")
    df = load_training_data(args.data)
    
    if len(df) < 50:
        print(f"WARNING: Only {len(df)} samples. Minimum 200+ recommended for reliable training.")
        print("Consider running 'python backtest_ml_data.py' with more samples or accumulating live trades.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(0)
    
    print("\nTraining LightGBM model...")
    model = train_model(df)
    save_model(model)
    
    print("\n✓ Training complete. Integrate model into signal_engine.py for inference.")


if __name__ == "__main__":
    main()
