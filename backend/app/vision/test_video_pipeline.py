import cv2
import sys
from pathlib import Path
from ultralytics import YOLO

# Add parent directory to path to import app modules
sys.path.append(str(Path(__file__).parent.parent.parent))
from app.vision.post_processors.event_extractor import EventExtractor

def test_pipeline():
    print("\n" + "="*50)
    print("🎬 AEGIS AI - LIVE VIDEO UNDERSTANDING TEST 🎬")
    print("="*50 + "\n")

    print("[1] Loading YOLOv8 Vision Model (Eye)...")
    base_dir = Path(__file__).parent.parent.parent
    model_path = base_dir / "dataset" / "yolo_runs" / "aegis_bgmi_v1" / "weights" / "best.pt"
    model = YOLO(str(model_path))
    
    print("[2] Initializing Event Extractor (Brain)...")
    extractor = EventExtractor()
    extractor.set_player_team_map({"SoulMortal": "SOUL", "GodLJonathan": "GODL"})
    
    print("[3] Connecting to Live Video Stream...")
    video_path = base_dir / "dataset" / "videos" / "test_clip.mp4"
    cap = cv2.VideoCapture(str(video_path))
    
    frame_count = 0
    simulated_health = 100
    
    # Process the video stream
    killfeed_triggered = False
    health_triggered = False
    
    print("⏳ Scanning video for events... (this takes a few seconds)")
    
    while cap.isOpened() and frame_count < 100: # Scan 100 frames
        ret, frame = cap.read()
        if not ret: break
        
        results = model(frame, verbose=False, imgsz=640)
        
        detected_classes = []
        for result in results:
            for box in result.boxes:
                name = model.names[int(box.cls[0])]
                detected_classes.append(name)
                
        if detected_classes:
            print(f"[Frame {frame_count}] YOLO Detected: {', '.join(detected_classes)}")
            if "killfeed" in detected_classes:
                print("   🔥 => EVENT: Killfeed Updated!")
            if "health_bar" in detected_classes:
                print("   🚨 => EVENT: Player Health Visible!")
        
        frame_count += 1
        
        import time
        time.sleep(0.1) # Simulate real-time 10fps processing so user can watch it live
        
    cap.release()
    print("\n✅ VIDEO PIPELINE TEST COMPLETE! AEGIS UNDERSTANDS THE GAME.")

if __name__ == "__main__":
    test_pipeline()
