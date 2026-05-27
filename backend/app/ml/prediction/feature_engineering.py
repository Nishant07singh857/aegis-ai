# backend/app/ml/prediction/feature_engineering.py
"""
AEGIS AI - Feature Engineering
Extracts and transforms raw game data into ML-ready features.
Handles normalization, encoding, and feature selection.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np
import pandas as pd

logger = structlog.get_logger("aegis.ml.features")


class FeatureType(str, Enum):
    """Types of features"""
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    TEMPORAL = "temporal"
    SPATIAL = "spatial"
    DERIVED = "derived"


@dataclass
class FeatureConfig:
    """Feature engineering configuration"""
    # Normalization
    normalize_numeric: bool = True
    normalization_method: str = "standard"  # standard, minmax, robust
    
    # Encoding
    encode_categorical: bool = True
    encoding_method: str = "onehot"  # onehot, label, target
    
    # Selection
    max_features: int = 50
    correlation_threshold: float = 0.95
    
    # Temporal
    window_sizes: List[float] = field(default_factory=lambda: [5, 10, 30, 60])
    include_rolling_stats: bool = True
    
    # Derived
    include_ratios: bool = True
    include_differences: bool = True
    include_interactions: bool = False


@dataclass
class FeatureSet:
    """Complete feature set"""
    features: np.ndarray = field(default_factory=lambda: np.array([]))
    feature_names: List[str] = field(default_factory=list)
    feature_types: Dict[str, FeatureType] = field(default_factory=dict)
    
    # Metadata
    num_samples: int = 0
    num_features: int = 0
    has_nans: bool = False
    created_at: float = field(default_factory=time.time)
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert to pandas DataFrame"""
        return pd.DataFrame(self.features, columns=self.feature_names)
    
    def get_feature(self, name: str) -> Optional[np.ndarray]:
        """Get a specific feature column"""
        if name in self.feature_names:
            idx = self.feature_names.index(name)
            return self.features[:, idx]
        return None
    
    def summary(self) -> Dict[str, Any]:
        """Get feature set summary"""
        return {
            "samples": self.num_samples,
            "features": self.num_features,
            "has_nans": self.has_nans,
            "types": {
                t.value: sum(1 for ft in self.feature_types.values() if ft == t)
                for t in FeatureType
            },
        }


class FeatureEngineer:
    """
    Extracts and transforms raw game data into ML features.
    
    Operations:
    - Numeric normalization (standard, min-max, robust)
    - Categorical encoding (one-hot, label, target)
    - Temporal feature extraction (rolling windows)
    - Spatial feature extraction (distances, zones)
    - Derived features (ratios, differences, interactions)
    - Feature selection (correlation, importance)
    """
    
    def __init__(self, config: Optional[FeatureConfig] = None):
        self.config = config or FeatureConfig()
        self.scaler: Optional[Any] = None
        self.encoders: Dict[str, Any] = {}
        self.selected_features: List[str] = []
        self._log = logger.bind(component="feature_engineer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FEATURE EXTRACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def extract_team_features(
        self,
        team_data: Dict[str, Any],
        prefix: str = "team",
    ) -> Dict[str, float]:
        """Extract features from team state"""
        
        features = {}
        
        # Basic
        features[f"{prefix}_players_alive"] = team_data.get("players_alive", 4)
        features[f"{prefix}_players_knocked"] = team_data.get("players_knocked", 0)
        features[f"{prefix}_total_hp"] = team_data.get("total_hp", 400)
        features[f"{prefix}_hp_percent"] = team_data.get("hp_percent", 100.0)
        
        # Combat
        features[f"{prefix}_kills"] = team_data.get("kills", 0)
        features[f"{prefix}_damage"] = team_data.get("damage", 0)
        features[f"{prefix}_knocks"] = team_data.get("knocks", 0)
        features[f"{prefix}_is_fighting"] = int(team_data.get("is_fighting", False))
        
        # Position
        pos = team_data.get("position", {})
        features[f"{prefix}_pos_x"] = pos.get("x", 0)
        features[f"{prefix}_pos_y"] = pos.get("y", 0)
        features[f"{prefix}_cover_quality"] = team_data.get("cover_quality", 50)
        features[f"{prefix}_has_high_ground"] = int(team_data.get("has_high_ground", False))
        features[f"{prefix}_in_compound"] = int(team_data.get("in_compound", False))
        
        # Resources
        utility = team_data.get("utility", {})
        features[f"{prefix}_smokes"] = utility.get("smokes", 0)
        features[f"{prefix}_grenades"] = utility.get("grenades", 0)
        features[f"{prefix}_molotovs"] = utility.get("molotovs", 0)
        features[f"{prefix}_healing"] = utility.get("healing", 0)
        
        return features
    
    def extract_match_features(
        self,
        match_data: Dict[str, Any],
    ) -> Dict[str, float]:
        """Extract features from match context"""
        
        return {
            "zone_phase": match_data.get("zone_phase", 1),
            "teams_alive": match_data.get("teams_alive", 16),
            "players_alive": match_data.get("players_alive", 64),
            "match_time": match_data.get("match_time", 0),
            "is_final_circle": int(match_data.get("is_final_circle", False)),
            "zone_radius": match_data.get("zone_radius", 4000),
        }
    
    def extract_fight_features(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, float]:
        """Extract features for fight prediction"""
        
        features = {}
        
        # Team A features
        features.update(self.extract_team_features(team_a, "team_a"))
        
        # Team B features
        features.update(self.extract_team_features(team_b, "team_b"))
        
        # Match context
        features.update(self.extract_match_features(context))
        
        # Derived features
        if self.config.include_differences:
            features["hp_diff"] = (
                features.get("team_a_hp_percent", 50) -
                features.get("team_b_hp_percent", 50)
            )
            features["player_diff"] = (
                features.get("team_a_players_alive", 4) -
                features.get("team_b_players_alive", 4)
            )
            features["damage_diff"] = (
                features.get("team_a_damage", 0) -
                features.get("team_b_damage", 0)
            )
        
        if self.config.include_ratios:
            features["hp_ratio"] = self._safe_ratio(
                features.get("team_a_hp_percent", 50),
                features.get("team_b_hp_percent", 50),
            )
            features["player_ratio"] = self._safe_ratio(
                features.get("team_a_players_alive", 4),
                features.get("team_b_players_alive", 4),
            )
        
        # Distance between teams
        team_a_pos = team_a.get("position", {})
        team_b_pos = team_b.get("position", {})
        
        ax = team_a_pos.get("x", 0)
        ay = team_a_pos.get("y", 0)
        bx = team_b_pos.get("x", 0)
        by = team_b_pos.get("y", 0)
        
        features["distance_between"] = np.sqrt((ax - bx)**2 + (ay - by)**2)
        
        return features
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRANSFORMATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def fit_normalizer(self, X: np.ndarray) -> None:
        """Fit normalizer on data"""
        
        if self.config.normalization_method == "standard":
            from sklearn.preprocessing import StandardScaler
            self.scaler = StandardScaler()
        elif self.config.normalization_method == "minmax":
            from sklearn.preprocessing import MinMaxScaler
            self.scaler = MinMaxScaler()
        elif self.config.normalization_method == "robust":
            from sklearn.preprocessing import RobustScaler
            self.scaler = RobustScaler()
        
        if self.scaler:
            self.scaler.fit(X)
            self._log.info(f"Normalizer fitted: {self.config.normalization_method}")
    
    def transform(self, X: np.ndarray) -> np.ndarray:
        """Transform features"""
        
        if self.scaler and self.config.normalize_numeric:
            return self.scaler.transform(X)
        return X
    
    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        """Fit and transform"""
        self.fit_normalizer(X)
        return self.transform(X)
    
    def add_rolling_features(
        self,
        features: Dict[str, float],
        history: List[Dict[str, float]],
        windows: Optional[List[float]] = None,
    ) -> Dict[str, float]:
        """Add rolling window statistics"""
        
        windows = windows or self.config.window_sizes
        
        if not history:
            return features
        
        df = pd.DataFrame(history)
        
        for window in windows:
            if len(df) < window:
                continue
            
            recent = df.tail(int(window))
            
            for col in recent.select_dtypes(include=[np.number]).columns:
                features[f"{col}_rolling_mean_{int(window)}s"] = recent[col].mean()
                features[f"{col}_rolling_std_{int(window)}s"] = recent[col].std()
                features[f"{col}_rolling_max_{int(window)}s"] = recent[col].max()
                features[f"{col}_rolling_min_{int(window)}s"] = recent[col].min()
        
        return features
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FEATURE SELECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def select_features(
        self,
        X: np.ndarray,
        feature_names: List[str],
        y: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, List[str]]:
        """Select most important features"""
        
        n_features = min(self.config.max_features, len(feature_names))
        
        if y is not None and len(feature_names) > n_features:
            try:
                from sklearn.feature_selection import mutual_info_classif
                
                importances = mutual_info_classif(X, y, random_state=42)
                indices = np.argsort(importances)[::-1][:n_features]
                
                X_selected = X[:, indices]
                names_selected = [feature_names[i] for i in indices]
                
                self.selected_features = names_selected
                
                self._log.info(f"Selected {len(names_selected)} features")
                
                return X_selected, names_selected
                
            except Exception as e:
                self._log.warning(f"Feature selection failed: {e}")
        
        self.selected_features = feature_names[:n_features]
        return X[:, :n_features], feature_names[:n_features]
    
    def remove_correlated(
        self,
        X: np.ndarray,
        feature_names: List[str],
    ) -> Tuple[np.ndarray, List[str]]:
        """Remove highly correlated features"""
        
        if len(feature_names) < 2:
            return X, feature_names
        
        try:
            corr_matrix = np.abs(np.corrcoef(X.T))
            
            to_remove = set()
            
            for i in range(len(feature_names)):
                for j in range(i + 1, len(feature_names)):
                    if corr_matrix[i, j] > self.config.correlation_threshold:
                        to_remove.add(j)
            
            keep_indices = [i for i in range(len(feature_names)) if i not in to_remove]
            
            X_filtered = X[:, keep_indices]
            names_filtered = [feature_names[i] for i in keep_indices]
            
            removed = len(feature_names) - len(names_filtered)
            
            if removed > 0:
                self._log.info(f"Removed {removed} correlated features")
            
            return X_filtered, names_filtered
            
        except Exception as e:
            self._log.warning(f"Correlation filter failed: {e}")
        
        return X, feature_names
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _safe_ratio(self, a: float, b: float, default: float = 1.0) -> float:
        """Safe division for ratios"""
        if b == 0:
            return default if a == 0 else float('inf')
        return a / b
    
    def save(self, path: str) -> None:
        """Save feature engineer state"""
        import joblib
        joblib.dump({
            "scaler": self.scaler,
            "encoders": self.encoders,
            "selected_features": self.selected_features,
            "config": self.config,
        }, path)
    
    def load(self, path: str) -> bool:
        """Load feature engineer state"""
        try:
            import joblib
            data = joblib.load(path)
            self.scaler = data.get("scaler")
            self.encoders = data.get("encoders", {})
            self.selected_features = data.get("selected_features", [])
            return True
        except Exception as e:
            self._log.error(f"Failed to load feature engineer: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get feature engineer statistics"""
        return {
            "normalization": self.config.normalization_method,
            "scaler_fitted": self.scaler is not None,
            "selected_features": len(self.selected_features),
            "config": self.config,
        }