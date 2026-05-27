import os
import json
import csv
from pydub import AudioSegment
import structlog

logger = structlog.get_logger("aegis.training.xtts")

class XTTSTrainer:
    """
    AEGIS AI - Voice Cloning Pipeline
    Processes audio and transcriptions into LJSpeech format and initiates XTTS fine-tuning.
    """
    def __init__(self, dataset_dir: str, output_dir: str):
        self.dataset_dir = dataset_dir
        self.output_dir = output_dir
        self.wavs_dir = os.path.join(output_dir, "wavs")
        os.makedirs(self.wavs_dir, exist_ok=True)
        
    def prepare_dataset(self, json_path: str):
        """Converts long MP3 + JSON transcript into short WAV chunks + metadata.csv"""
        if not os.path.exists(json_path):
            logger.error("JSON transcription file not found", path=json_path)
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        audio_path = data.get("source_file")
        if not audio_path or not os.path.exists(audio_path):
            logger.error("Source audio file not found", audio_path=audio_path)
            return

        logger.info("Loading full audio file into memory. This may take a minute...", audio_path=audio_path)
        # Load the large MP3
        audio = AudioSegment.from_file(audio_path)
        
        metadata_path = os.path.join(self.output_dir, "metadata.csv")
        segments = data.get("segments", [])
        
        logger.info("Slicing audio into chunks...", total_segments=len(segments))
        
        with open(metadata_path, "w", encoding="utf-8", newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter='|')
            
            for i, segment in enumerate(segments):
                start_ms = int(segment["start_time"] * 1000)
                end_ms = int(segment["end_time"] * 1000)
                text = segment["text"].strip()
                
                # Skip very short or empty segments
                if (end_ms - start_ms) < 1000 or not text:
                    continue
                    
                wav_filename = f"segment_{i:04d}"
                wav_path = os.path.join(self.wavs_dir, f"{wav_filename}.wav")
                
                # Slice and export as 22050Hz Mono WAV (standard for TTS)
                chunk = audio[start_ms:end_ms]
                chunk = chunk.set_frame_rate(22050).set_channels(1)
                chunk.export(wav_path, format="wav")
                
                # LJSpeech format: wav_file_name|text|normalized_text
                writer.writerow([wav_filename, text, text])
                
        logger.info("Dataset formatting complete!", metadata=metadata_path, output_dir=self.wavs_dir)

    def train_model(self):
        """
        Instructions to start the fine-tuning process.
        Running XTTS training programmatically requires a complex TrainerConfig.
        We recommend using the official TTS CLI or standard training scripts.
        """
        logger.info("================================================================")
        logger.info("🚀 XTTS DATASET PREPARED SUCCESSFULLY! ")
        logger.info("To begin fine-tuning, please ensure you have 'TTS' installed:")
        logger.info("  poetry install")
        logger.info("Then run the standard TTS training command with the generated LJSpeech dataset.")
        logger.info(f"Dataset location: {self.output_dir}")
        logger.info("Example command:")
        logger.info(f"  tts --model_name tts_models/multilingual/multi-dataset/xtts_v2 \\")
        logger.info(f"      --dataset_path {self.output_dir} \\")
        logger.info("      --run_path /tmp/xtts_output \\")
        logger.info("      --language_idx en")
        logger.info("================================================================")

if __name__ == "__main__":
    BASE_DIR = "/Users/nishantsingh/Downloads/aegis-ai-complete/backend/dataset"
    JSON_FILE = os.path.join(BASE_DIR, "audio", "actual_match_02_commentary.json")
    OUTPUT_DIR = os.path.join(BASE_DIR, "xtts_training_data")
    
    trainer = XTTSTrainer(dataset_dir=BASE_DIR, output_dir=OUTPUT_DIR)
    trainer.prepare_dataset(JSON_FILE)
    trainer.train_model()
