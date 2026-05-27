# backend/app/utils/string_utils.py
"""
AEGIS AI - String Utilities
String manipulation, text processing, and formatting helpers.
"""

import re
import hashlib
import unicodedata
from typing import List, Optional, Tuple
import structlog

logger = structlog.get_logger("aegis.utils.string")


class StringUtils:
    """String manipulation utilities"""
    
    @staticmethod
    def slugify(text: str) -> str:
        """Convert text to URL-friendly slug"""
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = re.sub(r'[^\w\s-]', '', text.lower())
        return re.sub(r'[-\s]+', '-', text).strip('-_')
    
    @staticmethod
    def truncate(text: str, max_length: int = 100, suffix: str = "...") -> str:
        """Truncate text to max length"""
        if len(text) <= max_length:
            return text
        return text[:max_length - len(suffix)] + suffix
    
    @staticmethod
    def camel_to_snake(text: str) -> str:
        """Convert CamelCase to snake_case"""
        result = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', text)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', result).lower()
    
    @staticmethod
    def snake_to_camel(text: str) -> str:
        """Convert snake_case to CamelCase"""
        return ''.join(word.capitalize() for word in text.split('_'))
    
    @staticmethod
    def extract_numbers(text: str) -> List[int]:
        """Extract all numbers from text"""
        return [int(n) for n in re.findall(r'\d+', text)]
    
    @staticmethod
    def extract_player_name(kill_feed_text: str) -> Optional[str]:
        """Extract player name from kill feed text"""
        # Common BGMI kill feed patterns
        patterns = [
            r'^(\w+)\s',           # Name at start
            r'(\w+)\s+eliminated',  # "Name eliminated"
            r'(\w+)\s+knocked',     # "Name knocked"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, kill_feed_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    @staticmethod
    def mask_sensitive(text: str, visible_chars: int = 4) -> str:
        """Mask sensitive text (API keys, passwords)"""
        if len(text) <= visible_chars:
            return '*' * len(text)
        return text[:visible_chars] + '*' * (len(text) - visible_chars)
    
    @staticmethod
    def hash_text(text: str, algorithm: str = "md5") -> str:
        """Hash text"""
        if algorithm == "md5":
            return hashlib.md5(text.encode()).hexdigest()
        elif algorithm == "sha256":
            return hashlib.sha256(text.encode()).hexdigest()
        return hashlib.md5(text.encode()).hexdigest()


class TextProcessing:
    """Text processing utilities"""
    
    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Simple word tokenization"""
        return re.findall(r'\b\w+\b', text.lower())
    
    @staticmethod
    def remove_stop_words(tokens: List[str], language: str = "english") -> List[str]:
        """Remove common stop words"""
        stop_words = {
            "english": {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                       "in", "on", "at", "to", "for", "of", "with", "by", "from",
                       "and", "or", "but", "not", "this", "that", "it", "he", "she"},
            "hindi": {"है", "हैं", "था", "थे", "की", "का", "के", "में", "पर", "और",
                     "एक", "यह", "वह", "इस", "उस", "को", "ने", "से", "तक", "भी"},
        }
        
        stop_set = stop_words.get(language, stop_words["english"])
        return [t for t in tokens if t not in stop_set]
    
    @staticmethod
    def word_frequency(tokens: List[str], top_n: int = 10) -> List[Tuple[str, int]]:
        """Get most frequent words"""
        from collections import Counter
        return Counter(tokens).most_common(top_n)
    
    @staticmethod
    def similarity(text1: str, text2: str) -> float:
        """Calculate text similarity (Jaccard)"""
        tokens1 = set(TextProcessing.tokenize(text1))
        tokens2 = set(TextProcessing.tokenize(text2))
        
        if not tokens1 and not tokens2:
            return 1.0
        
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        
        return len(intersection) / len(union) if union else 0.0