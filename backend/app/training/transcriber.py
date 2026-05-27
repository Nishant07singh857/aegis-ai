import os
import json
import whisper
import structlog

logger = structlog.get_logger("aegis.training.transcriber")

class AudioTranscriber:
    """
    AEGIS AI - Commentary Dataset Generator
    Uses OpenAI Whisper to transcribe esports commentary from audio files.
    The output is used to train the Storytelling/Commentary LLMs.
    """
    def __init__(self, model_size: str = "base"):
        logger.info("Loading Whisper Model...", size=model_size)
        # 'base' or 'small' are fast for M2 Mac CPU/GPU.
        self.model = whisper.load_model(model_size)
        logger.info("Model Loaded successfully.")

    def transcribe(self, audio_path: str, output_path: str):
        """Transcribes the audio file and saves the result as a structured JSON dataset."""
        if not os.path.exists(audio_path):
            logger.error("Audio file not found", path=audio_path)
            return
            
        logger.info("Starting transcription. This may take a few moments...", audio=audio_path)
        
        # Transcribe the audio
        result = self.model.transcribe(audio_path, fp16=False)
        
        # Format for dataset training
        dataset = {
            "source_file": audio_path,
            "full_text": result["text"],
            "segments": []
        }
        
        for segment in result["segments"]:
            dataset["segments"].append({
                "start_time": round(segment["start"], 2),
                "end_time": round(segment["end"], 2),
                "text": segment["text"].strip()
            })
            
        # Save to JSON
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(dataset, f, indent=4, ensure_ascii=False)
            
        logger.info("Transcription complete and saved!", output=output_path, total_segments=len(dataset["segments"]))


# ── Usage Example ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    audio_file = "/Users/nishantsingh/Downloads/aegis-ai-complete/backend/dataset/audio/actual_match_02.mp3"
    output_json = "/Users/nishantsingh/Downloads/aegis-ai-complete/backend/dataset/audio/actual_match_02_commentary.json"
    
    transcriber = AudioTranscriber(model_size="base")
    transcriber.transcribe(audio_file, output_json)
