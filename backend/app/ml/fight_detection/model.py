# backend/app/ml/fight_detection/model.py
"""
AEGIS AI - Fight Detection Model
Custom YOLO-based model architecture for BGMI fight detection.
Detects combat indicators: player interactions, damage effects, knocks.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("aegis.ml.fight_model")


@dataclass
class ModelConfig:
    """Fight detection model configuration"""
    # Architecture
    backbone: str = "cspdarknet53"  # Backbone network
    neck: str = "pan"               # Feature pyramid neck
    head: str = "decoupled"         # Detection head type
    depth_multiple: float = 0.67    # Model depth scaling
    width_multiple: float = 0.75    # Model width scaling
    
    # Input
    input_channels: int = 3
    input_size: Tuple[int, int] = (640, 640)
    
    # Classes
    num_classes: int = 7  # player_shooting, knocked, explosion, etc.
    class_names: List[str] = field(default_factory=lambda: [
        "player_shooting",
        "player_knocked", 
        "explosion",
        "muzzle_flash",
        "blood_effect",
        "damage_indicator",
        "knock_animation",
    ])
    
    # Anchors
    anchors: List[List[Tuple[int, int]]] = field(default_factory=lambda: [
        [(10, 13), (16, 30), (33, 23)],      # Small objects
        [(30, 61), (62, 45), (59, 119)],     # Medium objects
        [(116, 90), (156, 198), (373, 326)],  # Large objects
    ])
    
    # Detection
    strides: List[int] = field(default_factory=lambda: [8, 16, 32])
    num_outputs: int = 85  # 4 bbox + 1 obj + 80 classes (COCO base)
    
    # Training
    pretrained: bool = True
    pretrained_path: str = "yolov8m.pt"
    freeze_backbone: bool = False
    
    # Loss weights
    box_loss_weight: float = 7.5
    cls_loss_weight: float = 0.5
    dfl_loss_weight: float = 1.5
    
    # Optimization
    use_focal_loss: bool = True
    label_smoothing: float = 0.0
    
    # Fight-specific
    temporal_fusion: bool = True  # Fuse temporal information
    temporal_window: int = 5      # Frames to fuse
    attention_mechanism: str = "cbam"  # CBAM attention


class ConvBlock(nn.Module):
    """Standard convolution block with BN and activation"""
    
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int = 3,
        stride: int = 1,
        padding: int = None,
        groups: int = 1,
        activation: str = "silu",
    ):
        super().__init__()
        
        if padding is None:
            padding = kernel_size // 2
        
        self.conv = nn.Conv2d(
            in_channels, out_channels, kernel_size,
            stride, padding, groups=groups, bias=False,
        )
        self.bn = nn.BatchNorm2d(out_channels)
        
        if activation == "silu":
            self.act = nn.SiLU()
        elif activation == "relu":
            self.act = nn.ReLU(inplace=True)
        elif activation == "leaky_relu":
            self.act = nn.LeakyReLU(0.1, inplace=True)
        else:
            self.act = nn.Identity()
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.bn(self.conv(x)))


class Bottleneck(nn.Module):
    """Standard bottleneck block"""
    
    def __init__(self, in_channels: int, out_channels: int, shortcut: bool = True):
        super().__init__()
        
        hidden_channels = out_channels
        
        self.cv1 = ConvBlock(in_channels, hidden_channels, 1)
        self.cv2 = ConvBlock(hidden_channels, out_channels, 3)
        self.shortcut = shortcut and in_channels == out_channels
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.cv1(x)
        out = self.cv2(out)
        if self.shortcut:
            out = out + identity
        return out


class C2fBlock(nn.Module):
    """C2f block with cross-stage partial connections"""
    
    def __init__(self, in_channels: int, out_channels: int, num_bottlenecks: int = 1):
        super().__init__()
        
        hidden_channels = out_channels // 2
        
        self.cv1 = ConvBlock(in_channels, out_channels, 1)
        self.cv2 = ConvBlock((2 + num_bottlenecks) * hidden_channels, out_channels, 1)
        
        self.bottlenecks = nn.ModuleList([
            Bottleneck(hidden_channels, hidden_channels)
            for _ in range(num_bottlenecks)
        ])
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = list(self.cv1(x).chunk(2, dim=1))
        y.extend(m(y[-1]) for m in self.bottlenecks)
        return self.cv2(torch.cat(y, dim=1))


class SPPFBlock(nn.Module):
    """Spatial Pyramid Pooling - Fast version"""
    
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 5):
        super().__init__()
        
        hidden_channels = in_channels // 2
        
        self.cv1 = ConvBlock(in_channels, hidden_channels, 1)
        self.cv2 = ConvBlock(hidden_channels * 4, out_channels, 1)
        self.maxpool = nn.MaxPool2d(kernel_size=kernel_size, stride=1, padding=kernel_size // 2)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.cv1(x)
        y1 = self.maxpool(x)
        y2 = self.maxpool(y1)
        y3 = self.maxpool(y2)
        return self.cv2(torch.cat([x, y1, y2, y3], dim=1))


class CBAMAttention(nn.Module):
    """Convolutional Block Attention Module"""
    
    def __init__(self, channels: int, reduction: int = 16):
        super().__init__()
        
        # Channel attention
        self.channel_attention = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(channels, channels // reduction, 1),
            nn.ReLU(inplace=True),
            nn.Conv2d(channels // reduction, channels, 1),
            nn.Sigmoid(),
        )
        
        # Spatial attention
        self.spatial_attention = nn.Sequential(
            nn.Conv2d(2, 1, kernel_size=7, padding=3),
            nn.Sigmoid(),
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Channel attention
        ca = self.channel_attention(x)
        x = x * ca
        
        # Spatial attention
        avg_out = torch.mean(x, dim=1, keepdim=True)
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        sa = self.spatial_attention(torch.cat([avg_out, max_out], dim=1))
        x = x * sa
        
        return x


class TemporalFusion(nn.Module):
    """Temporal fusion module for fight detection"""
    
    def __init__(self, channels: int, window_size: int = 5):
        super().__init__()
        
        self.window_size = window_size
        
        # 3D convolution for temporal information
        self.temporal_conv = nn.Conv3d(
            channels, channels,
            kernel_size=(3, 1, 1),
            padding=(1, 0, 0),
            bias=False,
        )
        self.temporal_bn = nn.BatchNorm3d(channels)
        
        # Temporal attention
        self.temporal_attention = nn.Sequential(
            nn.AdaptiveAvgPool3d(1),
            nn.Conv3d(channels, channels // 4, 1),
            nn.ReLU(inplace=True),
            nn.Conv3d(channels // 4, channels, 1),
            nn.Sigmoid(),
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (B, T, C, H, W)
        if x.size(1) < 2:
            return x.squeeze(1)
        
        # Temporal convolution
        out = self.temporal_conv(x)
        out = F.relu(self.temporal_bn(out))
        
        # Temporal attention
        attn = self.temporal_attention(out)
        out = out * attn
        
        # Take last frame as output
        return out[:, -1]


class FightDetectionModel(nn.Module):
    """
    Custom fight detection model for BGMI.
    
    Architecture:
    - CSPDarknet backbone (YOLOv8-based)
    - PAN feature pyramid neck
    - Decoupled detection head
    - CBAM attention for combat-relevant features
    - Temporal fusion for fight sequence detection
    
    Detects:
    - Players actively shooting
    - Knocked players
    - Explosions and muzzle flashes
    - Blood effects and damage indicators
    - Knock animations
    """
    
    def __init__(self, config: Optional[ModelConfig] = None):
        super().__init__()
        
        self.config = config or ModelConfig()
        
        # Calculate channels based on width multiple
        base_channels = int(64 * self.config.width_multiple)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # BACKBONE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.stem = ConvBlock(
            self.config.input_channels, base_channels, 3, 2
        )
        
        self.stage1 = nn.Sequential(
            ConvBlock(base_channels, base_channels * 2, 3, 2),
            C2fBlock(base_channels * 2, base_channels * 2, 3),
        )
        
        self.stage2 = nn.Sequential(
            ConvBlock(base_channels * 2, base_channels * 4, 3, 2),
            C2fBlock(base_channels * 4, base_channels * 4, 6),
        )
        
        self.stage3 = nn.Sequential(
            ConvBlock(base_channels * 4, base_channels * 8, 3, 2),
            C2fBlock(base_channels * 8, base_channels * 8, 6),
        )
        
        self.stage4 = nn.Sequential(
            ConvBlock(base_channels * 8, base_channels * 16, 3, 2),
            C2fBlock(base_channels * 16, base_channels * 16, 3),
            SPPFBlock(base_channels * 16, base_channels * 16),
        )
        
        # CBAM attention for combat features
        self.attention_stage3 = CBAMAttention(base_channels * 8)
        self.attention_stage4 = CBAMAttention(base_channels * 16)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # NECK (Feature Pyramid)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Top-down
        self.neck_td1 = ConvBlock(base_channels * 16, base_channels * 8, 1)
        self.neck_td2 = ConvBlock(base_channels * 8, base_channels * 4, 1)
        
        # Upsample
        self.upsample = nn.Upsample(scale_factor=2, mode='nearest')
        
        # C2f for fused features
        self.neck_c2f1 = C2fBlock(base_channels * 16, base_channels * 8, 3)
        self.neck_c2f2 = C2fBlock(base_channels * 8, base_channels * 4, 3)
        
        # Bottom-up
        self.neck_bu1 = ConvBlock(base_channels * 4, base_channels * 4, 3, 2)
        self.neck_bu2 = ConvBlock(base_channels * 8, base_channels * 8, 3, 2)
        
        # C2f for bottom-up fused features
        self.neck_c2f3 = C2fBlock(base_channels * 8, base_channels * 8, 3)
        self.neck_c2f4 = C2fBlock(base_channels * 16, base_channels * 16, 3)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # TEMPORAL FUSION
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if self.config.temporal_fusion:
            self.temporal_fusion = TemporalFusion(
                base_channels * 4, self.config.temporal_window
            )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # DETECTION HEAD
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        num_outputs = 4 + self.config.num_classes  # bbox + class scores
        
        # Small object head (P3)
        self.head_p3 = nn.Sequential(
            ConvBlock(base_channels * 4, base_channels * 4, 3),
            nn.Conv2d(base_channels * 4, num_outputs * 3, 1),
        )
        
        # Medium object head (P4)
        self.head_p4 = nn.Sequential(
            ConvBlock(base_channels * 8, base_channels * 8, 3),
            nn.Conv2d(base_channels * 8, num_outputs * 3, 1),
        )
        
        # Large object head (P5)
        self.head_p5 = nn.Sequential(
            ConvBlock(base_channels * 16, base_channels * 16, 3),
            nn.Conv2d(base_channels * 16, num_outputs * 3, 1),
        )
        
        # Initialize weights
        self._initialize_weights()
        
        logger.info(
            f"FightDetectionModel initialized: "
            f"backbone={config.backbone}, "
            f"classes={config.num_classes}, "
            f"temporal_fusion={config.temporal_fusion}"
        )
    
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Forward pass.
        
        Args:
            x: Input tensor (B, C, H, W) or (B, T, C, H, W) with temporal fusion
            
        Returns:
            Tuple of (P3_output, P4_output, P5_output) for multi-scale detection
        """
        
        # Temporal fusion if enabled
        if self.config.temporal_fusion and x.dim() == 5:
            x = self.temporal_fusion(x)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # BACKBONE FORWARD
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        x = self.stem(x)           # P1/2
        p2 = self.stage1(x)        # P2/4
        p3 = self.stage2(p2)       # P3/8
        p4 = self.stage3(p3)       # P4/16
        p5 = self.stage4(p4)       # P5/32
        
        # Apply attention
        p3 = self.attention_stage3(p3)
        p4 = self.attention_stage4(p4)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # NECK FORWARD (Feature Pyramid)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Top-down pathway
        p5_td = self.neck_td1(p5)
        p4_fused = torch.cat([self.upsample(p5_td), p4], dim=1)
        p4_out = self.neck_c2f1(p4_fused)
        
        p4_td = self.neck_td2(p4_out)
        p3_fused = torch.cat([self.upsample(p4_td), p3], dim=1)
        p3_out = self.neck_c2f2(p3_fused)
        
        # Bottom-up pathway
        p3_bu = self.neck_bu1(p3_out)
        p4_fused_bu = torch.cat([p3_bu, p4_out], dim=1)
        p4_out_final = self.neck_c2f3(p4_fused_bu)
        
        p4_bu = self.neck_bu2(p4_out_final)
        p5_fused_bu = torch.cat([p4_bu, p5], dim=1)
        p5_out_final = self.neck_c2f4(p5_fused_bu)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # DETECTION HEAD
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        p3_det = self.head_p3(p3_out)
        p4_det = self.head_p4(p4_out_final)
        p5_det = self.head_p5(p5_out_final)
        
        return p3_det, p4_det, p5_det
    
    def _initialize_weights(self) -> None:
        """Initialize model weights"""
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
    
    def load_pretrained(self, path: str) -> None:
        """Load pretrained weights"""
        try:
            state_dict = torch.load(path, map_location='cpu')
            
            # Handle different checkpoint formats
            if 'model' in state_dict:
                state_dict = state_dict['model']
            if 'state_dict' in state_dict:
                state_dict = state_dict['state_dict']
            
            # Filter compatible layers
            model_dict = self.state_dict()
            compatible_dict = {}
            
            for k, v in state_dict.items():
                if k in model_dict and v.shape == model_dict[k].shape:
                    compatible_dict[k] = v
            
            self.load_state_dict(compatible_dict, strict=False)
            
            logger.info(f"Loaded pretrained weights from {path}: {len(compatible_dict)}/{len(model_dict)} layers")
            
        except Exception as e:
            logger.error(f"Failed to load pretrained weights: {e}")
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        total_params = sum(p.numel() for p in self.parameters())
        trainable_params = sum(p.numel() for p in self.parameters() if p.requires_grad)
        
        return {
            "architecture": self.config.backbone,
            "total_parameters": total_params,
            "trainable_parameters": trainable_params,
            "input_size": self.config.input_size,
            "num_classes": self.config.num_classes,
            "class_names": self.config.class_names,
            "temporal_fusion": self.config.temporal_fusion,
        }