# backend/app/utils/video_utils.py
"""
AEGIS AI - Video Processing Utilities
Video capture, encoding, clipping, and stream handling.
"""

import os
from typing import Dict, List, Optional, Any, Generator
from dataclasses import dataclass
from enum import Enum
import structlog
import numpy as np

logger = structlog.get_logger("aegis.utils.video")


class VideoCodec(str, Enum):
    """Video codecs"""
    H264 = "h264"
    H265 = "h265"
    VP9 = "vp9"
    AV1 = "av1"
    MJPG = "mjpg"


@dataclass
class VideoConfig:
    """Video processing configuration"""
    codec: VideoCodec = VideoCodec.H264
    fps: int = 30
    bitrate: int = 8000000  # 8 Mbps
    width: int = 1920
    height: int = 1080
    crf: int = 23  # Quality (lower = better, 18-28)


class VideoUtils:
    """Video processing utility functions"""
    
    @staticmethod
    def clip_video(
        input_path: str,
        output_path: str,
        start_time: float,
        duration: float,
        speed: float = 1.0,
    ) -> bool:
        """Clip a video segment"""
        try:
            import subprocess
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time),
                "-i", input_path,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-filter:v", f"setpts={1/speed}*PTS",
                "-an" if speed != 1.0 else "",
                output_path,
            ]
            
            cmd = [c for c in cmd if c]  # Remove empty strings
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Video clipped: {output_path}")
                return True
            else:
                logger.error(f"FFmpeg error: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Video clip failed: {e}")
            return False
    
    @staticmethod
    def extract_frames(
        video_path: str,
        output_dir: str,
        fps: Optional[int] = None,
        max_frames: int = 1000,
    ) -> List[str]:
        """Extract frames from video"""
        try:
            import cv2
            os.makedirs(output_dir, exist_ok=True)
            
            cap = cv2.VideoCapture(video_path)
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            
            frame_interval = 1
            if fps and fps < video_fps:
                frame_interval = int(video_fps / fps)
            
            frame_paths = []
            frame_count = 0
            
            while frame_count < max_frames:
                ret, frame = cap.read()
                if not ret:
                    break
                
                if frame_count % frame_interval == 0:
                    path = os.path.join(output_dir, f"frame_{frame_count:06d}.jpg")
                    cv2.imwrite(path, frame)
                    frame_paths.append(path)
                
                frame_count += 1
            
            cap.release()
            return frame_paths
            
        except Exception as e:
            logger.error(f"Frame extraction failed: {e}")
            return []
    
    @staticmethod
    def create_timelapse(
        image_dir: str,
        output_path: str,
        fps: int = 30,
        duration_seconds: float = 30.0,
    ) -> bool:
        """Create timelapse from images"""
        try:
            import cv2
            
            images = sorted([
                f for f in os.listdir(image_dir)
                if f.endswith(('.jpg', '.png'))
            ])
            
            if not images:
                return False
            
            total_frames = int(duration_seconds * fps)
            step = max(1, len(images) // total_frames)
            
            first = cv2.imread(os.path.join(image_dir, images[0]))
            h, w = first.shape[:2]
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
            
            for i in range(0, min(len(images), total_frames * step), step):
                img = cv2.imread(os.path.join(image_dir, images[i]))
                if img is not None:
                    if img.shape[:2] != (h, w):
                        img = cv2.resize(img, (w, h))
                    writer.write(img)
            
            writer.release()
            return True
            
        except Exception as e:
            logger.error(f"Timelapse creation failed: {e}")
            return False
    
    @staticmethod
    def merge_videos(
        input_paths: List[str],
        output_path: str,
        add_transitions: bool = False,
    ) -> bool:
        """Merge multiple videos into one"""
        try:
            import subprocess
            
            # Create concat file
            concat_file = output_path + ".concat.txt"
            with open(concat_file, 'w') as f:
                for path in input_paths:
                    f.write(f"file '{os.path.abspath(path)}'\n")
            
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                output_path,
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            os.remove(concat_file)
            
            return result.returncode == 0
            
        except Exception as e:
            logger.error(f"Video merge failed: {e}")
            return False
    
    @staticmethod
    def get_video_info(video_path: str) -> Optional[Dict[str, Any]]:
        """Get video metadata"""
        try:
            import cv2
            
            cap = cv2.VideoCapture(video_path)
            
            info = {
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": cap.get(cv2.CAP_PROP_FPS),
                "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                "duration_seconds": cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS),
                "codec": int(cap.get(cv2.CAP_PROP_FOURCC)),
                "file_size_mb": os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0,
            }
            
            cap.release()
            return info
            
        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            return None
    
    @staticmethod
    def stream_reader(url: str) -> Generator[np.ndarray, None, None]:
        """Read frames from video stream"""
        try:
            import cv2
            
            cap = cv2.VideoCapture(url)
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                yield frame
            
            cap.release()
            
        except Exception as e:
            logger.error(f"Stream reading failed: {e}")