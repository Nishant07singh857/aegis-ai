import os
import subprocess
import structlog

logger = structlog.get_logger("aegis.training.batch")

# User provided massive tournament VODs (Each 5+ hours, 6 matches per stream!)
# We will download the ENTIRE stream. The on-the-fly OpenCV cleaner will handle discarding lobby/breaks.
TOURNAMENT_LINKS = [
    {"id": "bgis_full_day_3", "url": "https://www.youtube.com/live/mjInkf-sz9Q?si=1KUZdCuxsqpg1lXh"},
    {"id": "bgis_full_day_4", "url": "https://www.youtube.com/live/lQg1F4hKHqg?si=cK6oVQAJ-2gsEpXV"}
]

def run_batch():
    logger.info("Starting Mass Data Collection with Smart On-the-fly Cleaning...", total_links=len(TOURNAMENT_LINKS))
    
    script_path = os.path.join(os.path.dirname(__file__), "data_collector.py")
    
    for match in TOURNAMENT_LINKS:
        logger.info(f"Processing FULL Stream: {match['id']}")
        
        # Run the updated data_collector which now automatically deletes useless frames on the fly!
        cmd = [
            "python", script_path, 
            match["url"], 
            match["id"]
        ]
        
        try:
            # We run this synchronously one by one so we don't crash the CPU/Memory
            subprocess.run(cmd, check=True)
            logger.info(f"Successfully finished processing {match['id']}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to process {match['id']}", error=str(e))
            
    logger.info("Batch Collection and Smart Filtration Complete! All clean data is in dataset/frames/")

if __name__ == "__main__":
    run_batch()
