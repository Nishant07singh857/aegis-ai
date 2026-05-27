import cv2
import time
import threading
import queue
import structlog
from typing import Optional, Union

logger = structlog.get_logger("aegis.vision.ingestion")

class FrameIngestionManager:
    """
    Dedicated Frame Ingestion Layer for AEGIS AI.
    Handles high-performance, asynchronous frame capturing from various sources
    (OBS Virtual Camera, RTMP Streams, Local Video, Scrcpy) and feeds them into
    a low-latency inference queue.
    """
    def __init__(self, source: Union[str, int], name: str = "main_feed", target_fps: int = 30, queue_size: int = 60):
        self.source = source
        self.name = name
        self.target_fps = target_fps
        self.frame_queue = queue.Queue(maxsize=queue_size)
        
        self.cap = None
        self.is_running = False
        self._thread = None
        
        # Performance tracking
        self.frames_read = 0
        self.frames_dropped = 0
        self.start_time = 0

    def _open_stream(self) -> bool:
        """Opens the video feed and configures optimal backend settings."""
        logger.info(f"[{self.name}] Attempting to connect to video feed...", source=self.source)
        
        # If source is scrcpy, we would normally pipe stdout. Here we use standard cv2 VideoCapture 
        # but optimized for minimal latency (cv2.CAP_FFMPEG or cv2.CAP_V4L2)
        if isinstance(self.source, str) and self.source.startswith("rtmp://"):
            # RTMP specific optimization
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        else:
            self.cap = cv2.VideoCapture(self.source)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2) # Reduce internal buffer to ensure real-time

        if not self.cap.isOpened():
            logger.error(f"[{self.name}] Failed to open video source: {self.source}")
            return False
            
        # Try forcing resolution if needed for YOLO
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"[{self.name}] Successfully connected to feed.", source_fps=fps)
        return True

    def _ingestion_loop(self):
        """Dedicated thread loop for fetching frames."""
        self.start_time = time.time()
        frame_time = 1.0 / self.target_fps

        while self.is_running:
            start_fetch = time.time()
            
            if self.cap is None or not self.cap.isOpened():
                logger.warning(f"[{self.name}] Feed lost. Attempting reconnect...")
                if not self._open_stream():
                    time.sleep(2)
                    continue

            ret, frame = self.cap.read()
            if not ret:
                logger.warning(f"[{self.name}] EOF or frame read error. Reconnecting...")
                self.cap.release()
                time.sleep(1)
                continue

            self.frames_read += 1

            # Push to queue. Drop old frames if inference is bottlenecking!
            # In live esports, latency is worse than missing a frame.
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                    self.frames_dropped += 1
                except queue.Empty:
                    pass
            
            self.frame_queue.put({"frame": frame, "timestamp": time.time(), "frame_id": self.frames_read})

            # Throttle ingestion to match target FPS (prevent CPU spin)
            elapsed = time.time() - start_fetch
            time_to_wait = frame_time - elapsed
            if time_to_wait > 0:
                time.sleep(time_to_wait)

    def start(self):
        """Starts the asynchronous ingestion pipeline."""
        if self.is_running:
            return
            
        if not self._open_stream():
            raise RuntimeError(f"[{self.name}] Could not start ingestion manager. Feed offline.")

        self.is_running = True
        self._thread = threading.Thread(target=self._ingestion_loop, daemon=True, name=f"Ingest-{self.name}")
        self._thread.start()
        logger.info(f"[{self.name}] Ingestion Manager started in background thread.")

    def stop(self):
        """Gracefully shuts down the ingestion pipeline."""
        logger.info(f"[{self.name}] Stopping Ingestion Manager...")
        self.is_running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
            
        elapsed = time.time() - self.start_time
        logger.info(f"[{self.name}] Ingestion Stopped.", 
                    total_time=round(elapsed, 2), 
                    frames_read=self.frames_read, 
                    frames_dropped=self.frames_dropped)

    def get_latest_frame(self) -> Optional[dict]:
        """Called by the GPU Inference pipeline to pull the next frame."""
        try:
            return self.frame_queue.get(timeout=0.1)
        except queue.Empty:
            return None


# ── Testing Example ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Example usage: Ingesting from local webcam or OBS Virtual Camera (device 0 or 1)
    # For a real tournament, this could be "rtmp://localhost:1935/live/stream"
    ingestion = FrameIngestionManager(source=0, name="OBS_Virtual_Cam", target_fps=30)
    
    try:
        ingestion.start()
        logger.info("Starting dummy inference consumer...")
        
        # Simulate the Inference loop consuming frames
        for _ in range(100):
            data = ingestion.get_latest_frame()
            if data:
                # e.g., result = yolo_model.predict(data["frame"])
                pass
            time.sleep(0.05) # simulate 20 FPS processing (slower than 30fps ingest)
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        ingestion.stop()
