import cv2
import sys
from ultralytics import YOLO
import structlog

logger = structlog.get_logger("aegis.vision.live_test")

def run_live_test():
    print("\n" + "="*50)
    print("🚀 AEGIS AI - REAL-TIME LIVE DEVICE TESTING 🚀")
    print("="*50 + "\n")

    print("[1] Loading YOLOv8 Vision Model...")
    # Load your trained custom model dynamically
    from pathlib import Path
    base_dir = Path(__file__).parent.parent.parent
    model_path = base_dir / "dataset" / "yolo_runs" / "aegis_bgmi_v1" / "weights" / "best.pt"
    model = YOLO(str(model_path))
    
    print("[2] Opening Live Camera...")
    # 0 = Default Mac Webcam
    # 1 or 2 = Usually OBS Virtual Camera or Capture Card (Elgato)
    # If 0 doesn't show your game, try changing it to 1!
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("❌ ERROR: Camera could not be opened. Check permissions!")
        sys.exit(1)

    print("\n✅ CAMERA ACTIVE! Point your device at the camera or mirror your screen.")
    print("💡 Press 'q' on your keyboard to stop the test.\n")

    # Lower resolution for super-fast live processing
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame")
            break
            
        # Run YOLO on the live frame
        results = model(frame, verbose=False, conf=0.4)
        
        # Draw bounding boxes directly on the frame
        annotated_frame = results[0].plot()
        
        # Get detected class names for terminal printing
        detected = []
        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            detected.append(model.names[cls_id])
            
        if detected:
            print(f"🔥 LIVE DETECTED: {', '.join(detected)}")

        # Display the live video feed
        cv2.imshow("AEGIS AI - Live Vision", annotated_frame)

        # Press 'q' to quit
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # Clean up
    cap.release()
    cv2.destroyAllWindows()
    print("\n🛑 Live testing stopped.")

if __name__ == "__main__":
    run_live_test()
