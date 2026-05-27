# backend/app/ml/prediction/ensemble.py
"""
AEGIS AI - Model Ensemble
Combines multiple prediction models for robust predictions.
Uses weighted voting, stacking, and confidence calibration.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog
import numpy as np

logger = structlog.get_logger("aegis.ml.ensemble")


class EnsembleMethod(str, Enum):
    """Ensemble combination methods"""
    WEIGHTED_AVERAGE = "weighted_average"
    VOTING = "voting"
    STACKING = "stacking"
    BOOSTING = "boosting"
    BAGGING = "bagging"


@dataclass
class EnsembleConfig:
    """Ensemble configuration"""
    method: EnsembleMethod = EnsembleMethod.WEIGHTED_AVERAGE
    
    # Model weights (higher = more influence)
    model_weights: Dict[str, float] = field(default_factory=dict)
    
    # Calibration
    calibrate_probabilities: bool = True
    calibration_method: str = "platt"  # platt, isotonic
    
    # Thresholds
    confidence_threshold: float = 0.5
    agreement_threshold: float = 0.6  # Models must agree this much
    
    # Stacking
    meta_model: Optional[Any] = None
    
    # Performance
    min_models_required: int = 2
    fallback_to_best: bool = True


@dataclass
class ModelPrediction:
    """Prediction from a single model"""
    model_name: str
    prediction: Any
    confidence: float
    probabilities: Optional[Dict[str, float]] = None
    inference_time_ms: float = 0.0
    weight: float = 1.0


@dataclass
class EnsemblePrediction:
    """Combined ensemble prediction"""
    # Final prediction
    prediction: Any
    confidence: float
    
    # Probabilities (if classification)
    probabilities: Optional[Dict[str, float]] = None
    
    # Ensemble details
    method: EnsembleMethod = EnsembleMethod.WEIGHTED_AVERAGE
    models_used: int = 0
    models_agreed: bool = False
    agreement_score: float = 0.0
    
    # Individual model predictions
    model_predictions: List[ModelPrediction] = field(default_factory=list)
    
    # Timing
    total_inference_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class ModelEnsemble:
    """
    Combines multiple prediction models for robust results.
    
    Methods:
    - Weighted average: Each model contributes by weight
    - Voting: Majority or weighted voting
    - Stacking: Meta-model combines base model outputs
    - Boosting: Sequential model improvement
    
    Features:
    - Confidence calibration
    - Model agreement checking
    - Fallback mechanisms
    - Performance tracking per model
    """
    
    def __init__(self, config: Optional[EnsembleConfig] = None):
        self.config = config or EnsembleConfig()
        
        # Registered models
        self.models: Dict[str, Any] = {}
        self.model_weights: Dict[str, float] = {}
        
        # Performance tracking
        self.model_accuracy: Dict[str, float] = {}
        self.model_count: Dict[str, int] = defaultdict(int)
        
        # Calibrator
        self.calibrator: Optional[Any] = None
        
        self._total_predictions = 0
        self._log = logger.bind(component="model_ensemble")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_model(
        self,
        name: str,
        model: Any,
        weight: float = 1.0,
        accuracy: float = 0.5,
    ) -> None:
        """Add a model to the ensemble"""
        self.models[name] = model
        self.model_weights[name] = weight
        self.model_accuracy[name] = accuracy
        
        self._log.info(f"Model added: {name} (weight={weight})")
    
    def remove_model(self, name: str) -> bool:
        """Remove a model from the ensemble"""
        if name in self.models:
            del self.models[name]
            del self.model_weights[name]
            self.model_accuracy.pop(name, None)
            return True
        return False
    
    def update_weight(self, name: str, weight: float) -> None:
        """Update model weight"""
        if name in self.model_weights:
            self.model_weights[name] = weight
    
    def update_accuracy(self, name: str, accuracy: float) -> None:
        """Update model accuracy tracking"""
        self.model_accuracy[name] = accuracy
        
        # Auto-adjust weight based on accuracy
        if self.config.method == EnsembleMethod.WEIGHTED_AVERAGE:
            self.model_weights[name] = max(0.1, accuracy)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ENSEMBLE PREDICTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def predict(
        self,
        features: Any,
        predict_fn: str = "predict",
    ) -> EnsemblePrediction:
        """
        Combine predictions from all models.
        
        Args:
            features: Input features
            predict_fn: Prediction function name
            
        Returns:
            EnsemblePrediction with combined result
        """
        
        if len(self.models) < self.config.min_models_required:
            self._log.warning(f"Not enough models: {len(self.models)}")
            return self._fallback_prediction(features)
        
        start_time = time.time()
        self._total_predictions += 1
        
        # Get predictions from each model
        model_predictions = []
        
        for name, model in self.models.items():
            try:
                t_start = time.time()
                
                if hasattr(model, predict_fn):
                    pred = getattr(model, predict_fn)(features)
                else:
                    pred = model(features)
                
                inference_time = (time.time() - t_start) * 1000
                
                # Extract confidence
                confidence = 0.5
                probabilities = None
                
                if hasattr(pred, 'confidence'):
                    confidence = pred.confidence
                if hasattr(pred, 'probabilities'):
                    probabilities = pred.probabilities
                
                mp = ModelPrediction(
                    model_name=name,
                    prediction=pred,
                    confidence=confidence,
                    probabilities=probabilities,
                    inference_time_ms=inference_time,
                    weight=self.model_weights.get(name, 1.0),
                )
                
                model_predictions.append(mp)
                self.model_count[name] += 1
                
            except Exception as e:
                self._log.error(f"Model {name} prediction failed: {e}")
        
        if not model_predictions:
            return self._fallback_prediction(features)
        
        # Combine predictions
        if self.config.method == EnsembleMethod.WEIGHTED_AVERAGE:
            combined = self._weighted_average(model_predictions, features)
        elif self.config.method == EnsembleMethod.VOTING:
            combined = self._voting(model_predictions, features)
        else:
            combined = self._weighted_average(model_predictions, features)
        
        # Calculate agreement
        agreement = self._calculate_agreement(model_predictions)
        
        total_time = (time.time() - start_time) * 1000
        
        return EnsemblePrediction(
            prediction=combined.get("prediction"),
            confidence=combined.get("confidence", 0.5),
            probabilities=combined.get("probabilities"),
            method=self.config.method,
            models_used=len(model_predictions),
            models_agreed=agreement >= self.config.agreement_threshold,
            agreement_score=round(agreement, 4),
            model_predictions=model_predictions,
            total_inference_time_ms=round(total_time, 2),
        )
    
    def _weighted_average(
        self,
        predictions: List[ModelPrediction],
        features: Any,
    ) -> Dict[str, Any]:
        """Weighted average combination"""
        
        total_weight = sum(p.weight for p in predictions)
        
        if total_weight == 0:
            return {"prediction": None, "confidence": 0.0}
        
        # Average confidence
        avg_confidence = sum(
            p.confidence * p.weight for p in predictions
        ) / total_weight
        
        # If classification with probabilities
        if predictions[0].probabilities:
            all_probs = defaultdict(float)
            
            for p in predictions:
                if p.probabilities:
                    for cls, prob in p.probabilities.items():
                        all_probs[cls] += prob * p.weight
            
            # Normalize
            total = sum(all_probs.values())
            if total > 0:
                for cls in all_probs:
                    all_probs[cls] /= total
            
            # Get best class
            best_class = max(all_probs, key=all_probs.get)
            
            return {
                "prediction": best_class,
                "confidence": all_probs[best_class],
                "probabilities": dict(all_probs),
            }
        
        # Regression or simple prediction
        return {
            "prediction": predictions[0].prediction,
            "confidence": avg_confidence,
        }
    
    def _voting(
        self,
        predictions: List[ModelPrediction],
        features: Any,
    ) -> Dict[str, Any]:
        """Voting-based combination"""
        
        # Count votes
        votes = defaultdict(float)
        
        for p in predictions:
            pred_str = str(p.prediction)
            votes[pred_str] += p.weight
        
        # Get winner
        winner = max(votes, key=votes.get)
        winner_votes = votes[winner]
        total_votes = sum(votes.values())
        
        confidence = winner_votes / total_votes if total_votes > 0 else 0.5
        
        return {
            "prediction": winner,
            "confidence": confidence,
        }
    
    def _calculate_agreement(self, predictions: List[ModelPrediction]) -> float:
        """Calculate how much models agree"""
        
        if len(predictions) < 2:
            return 1.0
        
        # For classification
        pred_values = [str(p.prediction) for p in predictions]
        
        # Find most common prediction
        from collections import Counter
        counts = Counter(pred_values)
        most_common_count = counts.most_common(1)[0][1]
        
        return most_common_count / len(predictions)
    
    def _fallback_prediction(self, features: Any) -> EnsemblePrediction:
        """Fallback when not enough models"""
        
        if self.models and self.config.fallback_to_best:
            # Use best model
            best_model = max(
                self.models.items(),
                key=lambda x: self.model_accuracy.get(x[0], 0),
            )
            
            try:
                pred = best_model[1](features)
                
                return EnsemblePrediction(
                    prediction=pred,
                    confidence=0.3,
                    models_used=1,
                    models_agreed=False,
                )
            except Exception:
                pass
        
        return EnsemblePrediction(
            prediction=None,
            confidence=0.0,
            models_used=0,
            models_agreed=False,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CALIBRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calibrate(self, predictions: np.ndarray, labels: np.ndarray) -> None:
        """Calibrate ensemble probabilities"""
        
        if not self.config.calibrate_probabilities:
            return
        
        try:
            if self.config.calibration_method == "platt":
                from sklearn.linear_model import LogisticRegression
                self.calibrator = LogisticRegression()
            elif self.config.calibration_method == "isotonic":
                from sklearn.isotonic import IsotonicRegression
                self.calibrator = IsotonicRegression(out_of_bounds='clip')
            
            if self.calibrator:
                self.calibrator.fit(predictions.reshape(-1, 1), labels)
                self._log.info("Ensemble calibrated")
                
        except Exception as e:
            self._log.error(f"Calibration failed: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_model_performance(self) -> Dict[str, Dict[str, Any]]:
        """Get performance metrics for each model"""
        return {
            name: {
                "weight": self.model_weights.get(name, 1.0),
                "accuracy": round(self.model_accuracy.get(name, 0.5), 4),
                "predictions": self.model_count.get(name, 0),
            }
            for name in self.models
        }
    
    def get_best_model(self) -> Optional[str]:
        """Get name of best performing model"""
        if not self.model_accuracy:
            return None
        return max(self.model_accuracy, key=self.model_accuracy.get)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get ensemble statistics"""
        return {
            "models": len(self.models),
            "method": self.config.method.value,
            "total_predictions": self._total_predictions,
            "calibrated": self.calibrator is not None,
            "model_performance": self.get_model_performance(),
            "best_model": self.get_best_model(),
        }