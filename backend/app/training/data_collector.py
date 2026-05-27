import cv2
import subprocess
import structlog
from pathlib import Path

logger = structlog.get_logger("aegis.training.collector")

class YouTubeDataCollector:
    """
    AEGIS AI - Training Data Collector
    Downloads YouTube VODs and extracts:
    1. Frames (for YOLOv8 Vision Model training)
    2. Audio (for Whisper -> LLM Commentary Model training)
    """
    def __init__(self, output_dir: str = "dataset"):
        self.output_dir = Path(output_dir)
        self.frames_dir = self.output_dir / "frames"
        self.audio_dir = self.output_dir / "audio"
        self.video_dir = self.output_dir / "videos"
        
        # Create directories if they don't exist
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def download_youtube_video(self, url: str, video_id: str, duration: str = None) -> str:
        """Downloads highest quality video + audio using yt-dlp"""
        output_path = self.video_dir / f"{video_id}.mp4"
        if output_path.exists():
            logger.info("Video already downloaded", path=str(output_path))
            return str(output_path)
            
        logger.info("Downloading YouTube VOD...", url=url)
        # Requires yt-dlp installed (`pip install yt-dlp`)
        command = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-o", str(output_path)
        ]
        
        if duration:
            # Download a specific section, e.g., "*00:00-03:00" for first 3 mins
            command.extend(["--download-sections", f"*{duration}"])
            
        command.append(url)
        
        try:
            subprocess.run(command, check=True)
            logger.info("Download complete", path=str(output_path))
            return str(output_path)
        except subprocess.CalledProcessError as e:
            logger.error("Download failed", error=str(e))
            raise

    def extract_audio(self, video_path: str, video_id: str) -> str:
        """Extracts MP3 audio from the video for Whisper transcription"""
        audio_path = self.audio_dir / f"{video_id}.mp3"
        if audio_path.exists():
            logger.info("Audio already extracted", path=str(audio_path))
            return str(audio_path)
            
        logger.info("Extracting audio for Commentary training...", video=video_path)
        # Requires ffmpeg installed
        command = [
            "ffmpeg", "-i", video_path, 
            "-q:a", "0", "-map", "a", 
            str(audio_path), "-y"
        ]
        
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Audio extracted", path=str(audio_path))
        return str(audio_path)

    def is_gameplay_frame(self, frame) -> bool:
        """
        Uses heuristics to determine if a frame is actual BGMI gameplay.
        """
        import numpy as np
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        edges = cv2.Canny(gray, 100, 200)
        edge_density = np.sum(edges) / 255.0 / (frame.shape[0] * frame.shape[1])
        mean_brightness = np.mean(gray)
        
        if edge_density < 0.02 or mean_brightness < 15:
            return False
        return True

    def extract_frames(self, video_path: str, video_id: str, frame_interval: int = 30):
        """
        Extracts frames from the video for Computer Vision labeling.
        Filters out useless frames ON THE FLY to save storage.
        """
        logger.info("Extracting and Smart-Filtering frames...", video=video_path)
        cap = cv2.VideoCapture(video_path)
        
        count = 0
        saved = 0
        skipped = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            if count % frame_interval == 0:
                if self.is_gameplay_frame(frame):
                    frame_path = self.frames_dir / f"{video_id}_frame_{saved:05d}.jpg"
                    cv2.imwrite(str(frame_path), frame)
                    saved += 1
                else:
                    skipped += 1
                
            count += 1
            
        cap.release()
        logger.info("Frame extraction complete", total_saved=saved, total_skipped_junk=skipped)


# ── Usage Example ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python data_collector.py <YOUTUBE_URL> <VIDEO_ID> [DURATION]")
        print("Example: python data_collector.py 'https://www.youtube.com/watch?v=XYZ' pmgc_test '00:00-02:00'")
        sys.exit(1)
        
    url = sys.argv[1]
    vid_id = sys.argv[2]
    duration = sys.argv[3] if len(sys.argv) > 3 else None
    
    collector = YouTubeDataCollector()
    
    # 1. Download
    video_file = collector.download_youtube_video(url, vid_id, duration)
    
    # 2. Extract Audio for Commentary AI
    collector.extract_audio(video_file, vid_id)
    
    # 3. Extract Frames for Vision AI (1 frame per second assuming 60fps feed -> 60 interval)
    collector.extract_frames(video_file, vid_id, frame_interval=60)
