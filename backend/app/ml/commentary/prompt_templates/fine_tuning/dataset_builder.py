# backend/app/ml/commentary/fine_tuning/dataset_builder.py
"""
AEGIS AI - Commentary Dataset Builder
Builds fine-tuning datasets from match recordings,
commentary transcripts, and esports broadcasts.
"""

import os
import json
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog
import torch
from torch.utils.data import Dataset

logger = structlog.get_logger("aegis.ml.finetune")


class DataFormat(str, Enum):
    """Dataset formats"""
    ALPACA = "alpaca"         # Instruction/Input/Output format
    SHAREGPT = "sharegpt"     # Multi-turn conversation format
    CHATML = "chatml"         # Chat markup language
    CUSTOM = "custom"         # Custom AEGIS format
    COMPLETION = "completion"  # Simple text completion


@dataclass
class CommentaryExample:
    """A single commentary training example"""
    example_id: str
    
    # Input context
    event_type: str = ""
    match_context: str = ""
    player_context: str = ""
    team_context: str = ""
    
    # Instruction
    instruction: str = ""
    style: str = "hinglish"
    tone: str = "excited"
    language: str = "hinglish"
    
    # Output
    commentary_text: str = ""
    
    # Metadata
    source: str = ""  # tournament, scrim, youtube, etc.
    caster_name: str = ""
    match_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Quality
    quality_score: float = 0.8  # Human-rated quality
    is_verified: bool = False
    
    def to_alpaca_format(self) -> Dict[str, str]:
        """Convert to Alpaca format"""
        return {
            "instruction": self.instruction,
            "input": self._build_input_text(),
            "output": self.commentary_text,
        }
    
    def to_sharegpt_format(self) -> Dict[str, Any]:
        """Convert to ShareGPT format"""
        return {
            "conversations": [
                {"from": "human", "value": self._build_input_text()},
                {"from": "gpt", "value": self.commentary_text},
            ],
        }
    
    def to_chatml_format(self) -> str:
        """Convert to ChatML format"""
        return (
            f"<|im_start|>system\nYou are a professional BGMI esports caster.\n<|im_end|>\n"
            f"<|im_start|>user\n{self._build_input_text()}\n<|im_end|>\n"
            f"<|im_start|>assistant\n{self.commentary_text}\n<|im_end|>"
        )
    
    def _build_input_text(self) -> str:
        """Build input text from context"""
        parts = []
        
        if self.event_type:
            parts.append(f"Event: {self.event_type}")
        
        if self.match_context:
            parts.append(f"Match: {self.match_context}")
        
        if self.player_context:
            parts.append(f"Player: {self.player_context}")
        
        if self.team_context:
            parts.append(f"Team: {self.team_context}")
        
        if self.style:
            parts.append(f"Style: {self.style}")
        
        if self.tone:
            parts.append(f"Tone: {self.tone}")
        
        return "\n".join(parts) if parts else self.instruction


class CommentaryDataset(Dataset):
    """PyTorch dataset for commentary fine-tuning"""
    
    def __init__(
        self,
        examples: List[CommentaryExample],
        tokenizer: Any,
        max_length: int = 512,
        format: DataFormat = DataFormat.ALPACA,
    ):
        self.examples = examples
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.format = format
    
    def __len__(self) -> int:
        return len(self.examples)
    
    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        example = self.examples[idx]
        
        # Format based on type
        if self.format == DataFormat.ALPACA:
            text = self._format_alpaca(example)
        elif self.format == DataFormat.SHAREGPT:
            text = self._format_sharegpt(example)
        elif self.format == DataFormat.CHATML:
            text = example.to_chatml_format()
        else:
            text = self._format_alpaca(example)
        
        # Tokenize
        tokens = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        
        return {
            "input_ids": tokens["input_ids"].squeeze(0),
            "attention_mask": tokens["attention_mask"].squeeze(0),
            "labels": tokens["input_ids"].squeeze(0).clone(),
        }
    
    def _format_alpaca(self, example: CommentaryExample) -> str:
        """Format as Alpaca-style prompt"""
        data = example.to_alpaca_format()
        
        return (
            f"### Instruction:\n{data['instruction']}\n\n"
            f"### Input:\n{data['input']}\n\n"
            f"### Response:\n{data['output']}"
        )
    
    def _format_sharegpt(self, example: CommentaryExample) -> str:
        """Format as ShareGPT-style conversation"""
        data = example.to_sharegpt_format()
        
        text = ""
        for turn in data["conversations"]:
            role = "Human" if turn["from"] == "human" else "Assistant"
            text += f"{role}: {turn['value']}\n"
        
        return text


class DatasetBuilder:
    """
    Builds fine-tuning datasets from multiple sources.
    
    Sources:
    - Tournament VODs with commentary
    - Professional caster recordings
    - Community-casted matches
    - YouTube BGMI content
    - Manual annotations
    
    Features:
    - Multi-format export (Alpaca, ShareGPT, ChatML)
    - Quality filtering
    - Language detection
    - Deduplication
    - Train/val/test splitting
    - Data augmentation
    """
    
    def __init__(self):
        self.examples: List[CommentaryExample] = []
        self.stats: Dict[str, Any] = defaultdict(int)
        self._log = logger.bind(component="dataset_builder")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA LOADING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_from_jsonl(self, filepath: str, format: DataFormat = DataFormat.ALPACA) -> int:
        """Load examples from JSONL file"""
        
        loaded = 0
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    
                    if format == DataFormat.ALPACA:
                        example = self._parse_alpaca(data)
                    elif format == DataFormat.SHAREGPT:
                        example = self._parse_sharegpt(data)
                    else:
                        example = self._parse_custom(data)
                    
                    if example:
                        self.examples.append(example)
                        loaded += 1
                        
                except json.JSONDecodeError:
                    continue
        
        self.stats["loaded_from_jsonl"] += loaded
        logger.info(f"Loaded {loaded} examples from {filepath}")
        return loaded
    
    def load_from_tournament_vods(
        self,
        vod_data: List[Dict[str, Any]],
        caster_name: str = "",
    ) -> int:
        """Load examples from tournament VOD commentary"""
        
        loaded = 0
        
        for vod in vod_data:
            # Extract commentary segments
            segments = vod.get("commentary_segments", [])
            
            for segment in segments:
                example = CommentaryExample(
                    example_id=f"vod_{vod.get('match_id', 'unknown')}_{loaded}",
                    event_type=segment.get("event_type", ""),
                    match_context=self._build_match_context(vod),
                    player_context=segment.get("player_context", ""),
                    team_context=segment.get("team_context", ""),
                    instruction=f"Commentate on this {segment.get('event_type', 'event')}",
                    style=segment.get("style", "hinglish"),
                    tone=segment.get("tone", "excited"),
                    commentary_text=segment.get("commentary", ""),
                    source="tournament_vod",
                    caster_name=caster_name,
                    match_id=vod.get("match_id", ""),
                    quality_score=segment.get("quality", 0.8),
                )
                
                self.examples.append(example)
                loaded += 1
        
        self.stats["loaded_from_vods"] += loaded
        logger.info(f"Loaded {loaded} examples from tournament VODs")
        return loaded
    
    def load_from_youtube_transcripts(
        self,
        transcripts: List[Dict[str, Any]],
    ) -> int:
        """Load examples from YouTube BGMI commentary transcripts"""
        
        loaded = 0
        
        for transcript in transcripts:
            # Parse commentary segments
            lines = transcript.get("text", "").split("\n")
            
            for line in lines:
                if len(line.strip()) < 10:
                    continue
                
                # Detect if this is commentary (has exclamation, player names, etc.)
                if self._is_commentary_line(line):
                    example = CommentaryExample(
                        example_id=f"yt_{transcript.get('video_id', 'unknown')}_{loaded}",
                        commentary_text=line.strip(),
                        source="youtube",
                        caster_name=transcript.get("channel", ""),
                        quality_score=0.6,  # Lower confidence for auto-extracted
                    )
                    
                    self.examples.append(example)
                    loaded += 1
        
        self.stats["loaded_from_youtube"] += loaded
        logger.info(f"Loaded {loaded} examples from YouTube transcripts")
        return loaded
    
    def load_manual_annotations(self, filepath: str) -> int:
        """Load manually annotated examples"""
        
        loaded = 0
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for ann in data.get("annotations", []):
            example = CommentaryExample(
                example_id=f"manual_{ann.get('id', loaded)}",
                event_type=ann.get("event_type", ""),
                match_context=ann.get("match_context", ""),
                player_context=ann.get("player_context", ""),
                team_context=ann.get("team_context", ""),
                instruction=ann.get("instruction", ""),
                style=ann.get("style", "hinglish"),
                tone=ann.get("tone", "excited"),
                commentary_text=ann.get("commentary", ""),
                source="manual",
                caster_name=ann.get("caster", ""),
                quality_score=ann.get("quality", 1.0),
                is_verified=True,
            )
            
            self.examples.append(example)
            loaded += 1
        
        self.stats["loaded_manual"] += loaded
        logger.info(f"Loaded {loaded} manual annotations from {filepath}")
        return loaded
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def filter_by_quality(self, min_quality: float = 0.5) -> int:
        """Filter out low-quality examples"""
        
        before = len(self.examples)
        self.examples = [e for e in self.examples if e.quality_score >= min_quality]
        removed = before - len(self.examples)
        
        self.stats["filtered_by_quality"] = removed
        logger.info(f"Quality filter: removed {removed} examples (min quality: {min_quality})")
        return removed
    
    def filter_by_language(self, language: str) -> int:
        """Filter examples by language"""
        
        before = len(self.examples)
        
        # Simple language detection based on script
        if language == "hindi":
            import re
            hindi_pattern = re.compile(r'[\u0900-\u097F]')
            self.examples = [
                e for e in self.examples
                if hindi_pattern.search(e.commentary_text)
            ]
        elif language == "english":
            self.examples = [
                e for e in self.examples
                if all(ord(c) < 128 for c in e.commentary_text)
            ]
        
        removed = before - len(self.examples)
        self.stats[f"filtered_language_{language}"] = removed
        return removed
    
    def deduplicate(self, similarity_threshold: float = 0.9) -> int:
        """Remove duplicate examples"""
        
        before = len(self.examples)
        unique = []
        seen_texts = set()
        
        for example in self.examples:
            # Simple exact dedup for now
            text_hash = hash(example.commentary_text.lower().strip())
            
            if text_hash not in seen_texts:
                seen_texts.add(text_hash)
                unique.append(example)
        
        self.examples = unique
        removed = before - len(self.examples)
        
        self.stats["deduplicated"] = removed
        logger.info(f"Deduplication: removed {removed} duplicates")
        return removed
    
    def augment_data(self, multiplier: int = 2) -> int:
        """Augment dataset with variations"""
        
        added = 0
        original_count = len(self.examples)
        
        for i in range(original_count):
            original = self.examples[i]
            
            # Create variations with different styles/tones
            for _ in range(multiplier - 1):
                import random
                
                styles = ["hinglish", "hindi", "english"]
                tones = ["excited", "tense", "dramatic", "celebratory"]
                
                variant = CommentaryExample(
                    example_id=f"{original.example_id}_aug_{added}",
                    event_type=original.event_type,
                    match_context=original.match_context,
                    player_context=original.player_context,
                    team_context=original.team_context,
                    instruction=original.instruction,
                    style=random.choice(styles),
                    tone=random.choice(tones),
                    commentary_text=original.commentary_text,
                    source=f"{original.source}_augmented",
                    quality_score=original.quality_score * 0.9,
                )
                
                self.examples.append(variant)
                added += 1
        
        self.stats["augmented"] = added
        logger.info(f"Data augmentation: added {added} variants")
        return added
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EXPORT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def split_dataset(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
        shuffle: bool = True,
        seed: int = 42,
    ) -> Tuple[List[CommentaryExample], List[CommentaryExample], List[CommentaryExample]]:
        """Split dataset into train/val/test"""
        
        import random
        random.seed(seed)
        
        examples = self.examples.copy()
        
        if shuffle:
            random.shuffle(examples)
        
        n = len(examples)
        train_end = int(n * train_ratio)
        val_end = train_end + int(n * val_ratio)
        
        train = examples[:train_end]
        val = examples[train_end:val_end]
        test = examples[val_end:]
        
        logger.info(f"Dataset split: train={len(train)}, val={len(val)}, test={len(test)}")
        
        return train, val, test
    
    def export_to_jsonl(
        self,
        filepath: str,
        format: DataFormat = DataFormat.ALPACA,
        examples: Optional[List[CommentaryExample]] = None,
    ) -> str:
        """Export examples to JSONL file"""
        
        examples = examples or self.examples
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for example in examples:
                if format == DataFormat.ALPACA:
                    data = example.to_alpaca_format()
                elif format == DataFormat.SHAREGPT:
                    data = example.to_sharegpt_format()
                else:
                    data = {"text": example.to_chatml_format()}
                
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
        
        logger.info(f"Exported {len(examples)} examples to {filepath}")
        return filepath
    
    def export_splits(
        self,
        output_dir: str,
        format: DataFormat = DataFormat.ALPACA,
        train_ratio: float = 0.8,
    ) -> Dict[str, str]:
        """Export train/val/test splits"""
        
        train, val, test = self.split_dataset(train_ratio=train_ratio)
        
        files = {
            "train": self.export_to_jsonl(
                os.path.join(output_dir, "train.jsonl"), format, train
            ),
            "val": self.export_to_jsonl(
                os.path.join(output_dir, "val.jsonl"), format, val
            ),
            "test": self.export_to_jsonl(
                os.path.join(output_dir, "test.jsonl"), format, test
            ),
        }
        
        return files
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HELPERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _parse_alpaca(self, data: Dict[str, Any]) -> Optional[CommentaryExample]:
        """Parse Alpaca format data"""
        
        return CommentaryExample(
            example_id=f"alpaca_{hash(json.dumps(data))}",
            instruction=data.get("instruction", ""),
            event_type=self._extract_event_type(data.get("input", "")),
            commentary_text=data.get("output", ""),
            source="alpaca_import",
        )
    
    def _parse_sharegpt(self, data: Dict[str, Any]) -> Optional[CommentaryExample]:
        """Parse ShareGPT format data"""
        
        conversations = data.get("conversations", [])
        
        if len(conversations) >= 2:
            return CommentaryExample(
                example_id=f"sharegpt_{hash(json.dumps(data))}",
                event_type=self._extract_event_type(conversations[0].get("value", "")),
                commentary_text=conversations[-1].get("value", ""),
                source="sharegpt_import",
            )
        
        return None
    
    def _parse_custom(self, data: Dict[str, Any]) -> Optional[CommentaryExample]:
        """Parse custom AEGIS format data"""
        
        return CommentaryExample(
            example_id=data.get("id", f"custom_{hash(json.dumps(data))}"),
            event_type=data.get("event_type", ""),
            match_context=data.get("match_context", ""),
            player_context=data.get("player_context", ""),
            team_context=data.get("team_context", ""),
            instruction=data.get("instruction", ""),
            style=data.get("style", "hinglish"),
            tone=data.get("tone", "excited"),
            language=data.get("language", "hinglish"),
            commentary_text=data.get("commentary", ""),
            source=data.get("source", "custom"),
            caster_name=data.get("caster", ""),
            quality_score=data.get("quality", 0.8),
            is_verified=data.get("verified", False),
        )
    
    def _build_match_context(self, vod: Dict[str, Any]) -> str:
        """Build match context string"""
        parts = []
        
        if vod.get("tournament_name"):
            parts.append(f"Tournament: {vod['tournament_name']}")
        if vod.get("match_name"):
            parts.append(f"Match: {vod['match_name']}")
        if vod.get("map_name"):
            parts.append(f"Map: {vod['map_name']}")
        if vod.get("teams_alive"):
            parts.append(f"Teams: {vod['teams_alive']}")
        
        return " | ".join(parts)
    
    def _extract_event_type(self, text: str) -> str:
        """Extract event type from text"""
        
        event_keywords = {
            "kill": ["kill", "eliminated", "elimination", "finish"],
            "multi_kill": ["multi kill", "double kill", "triple kill", "rampage"],
            "knock": ["knock", "knocked", "down"],
            "team_wipe": ["team wipe", "squad wipe", "wiped", "eliminated"],
            "clutch": ["clutch", "1v", "alone"],
            "fight": ["fight", "battle", "engage", "combat"],
            "grenade": ["grenade", "nade", "explosive"],
            "headshot": ["headshot", "head shot", "one tap"],
            "long_range": ["long range", "snipe", "distance"],
            "zone": ["zone", "circle", "blue zone"],
            "winner": ["winner", "chicken dinner", "victory", "champion"],
        }
        
        text_lower = text.lower()
        
        for event_type, keywords in event_keywords.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return event_type
        
        return "general"
    
    def _is_commentary_line(self, text: str) -> bool:
        """Check if a text line is likely commentary"""
        
        # Has exclamation marks (excitement)
        if "!" in text:
            return True
        
        # Contains player/team references
        commentary_indicators = [
            "kill", "eliminated", "knock", "clutch", "fight",
            "zone", "circle", "winner", "chicken dinner",
            "bhai", "arey", "kya", "what a", "amazing",
        ]
        
        text_lower = text.lower()
        return any(indicator in text_lower for indicator in commentary_indicators)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get dataset statistics"""
        
        # Count by event type
        event_counts = defaultdict(int)
        style_counts = defaultdict(int)
        source_counts = defaultdict(int)
        
        for example in self.examples:
            event_counts[example.event_type] += 1
            style_counts[example.style] += 1
            source_counts[example.source] += 1
        
        return {
            "total_examples": len(self.examples),
            "verified_examples": sum(1 for e in self.examples if e.is_verified),
            "avg_quality": round(
                sum(e.quality_score for e in self.examples) / max(1, len(self.examples)), 2
            ),
            "by_event_type": dict(event_counts),
            "by_style": dict(style_counts),
            "by_source": dict(source_counts),
            "processing_stats": dict(self.stats),
        }
    
    def reset(self) -> None:
        """Reset dataset builder"""
        self.examples.clear()
        self.stats.clear()
        self._log.info("Dataset builder reset")