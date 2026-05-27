# backend/app/ml/commentary/llm_chain.py
"""
AEGIS AI - LLM Chain for Commentary Generation
LangChain-based pipeline for generating esports commentary.
Handles prompt construction, LLM invocation, and response parsing.
"""

import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.ml.commentary")


class CommentaryStyle(str, Enum):
    """Commentary broadcasting styles"""
    HYPE = "hype"              # Energetic, fast-paced
    ANALYTICAL = "analytical"  # Tactical breakdown
    HINDI = "hindi"           # Pure Hindi esports
    HINGLISH = "hinglish"     # Mixed Hindi-English (most popular)
    CINEMATIC = "cinematic"   # Documentary-style
    CASUAL = "casual"         # Relaxed, conversational
    PLAY_BY_PLAY = "play_by_play"  # Action-focused
    COLOR = "color"           # Analysis between action


class CommentaryTone(str, Enum):
    """Emotional tone of commentary"""
    EXCITED = "excited"
    TENSE = "tense"
    ANALYTICAL = "analytical"
    CELEBRATORY = "celebratory"
    SHOCKED = "shocked"
    CASUAL = "casual"
    DRAMATIC = "dramatic"
    RESPECTFUL = "respectful"


class LLMProvider(str, Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL_LLAMA = "local_llama"
    LOCAL_MISTRAL = "local_mistral"
    VLLM = "vllm"
    TGI = "tgi"  # Text Generation Inference


@dataclass
class ChainConfig:
    """LLM Chain configuration"""
    # Provider
    provider: LLMProvider = LLMProvider.OPENAI
    model_name: str = "gpt-4"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    
    # Generation
    max_tokens: int = 150
    temperature: float = 0.8
    top_p: float = 0.95
    frequency_penalty: float = 0.3
    presence_penalty: float = 0.3
    stop_sequences: List[str] = field(default_factory=lambda: ["\n\n", "###"])
    
    # Chain
    max_retries: int = 3
    retry_delay: float = 1.0
    request_timeout: float = 10.0
    
    # Context
    max_context_length: int = 2000
    max_recent_commentary: int = 5
    
    # Caching
    enable_cache: bool = True
    cache_ttl: float = 60.0  # Seconds
    cache_max_size: int = 500
    
    # Rate limiting
    max_requests_per_minute: int = 60
    max_tokens_per_minute: int = 10000
    
    # Languages
    default_language: str = "hinglish"
    supported_languages: List[str] = field(default_factory=lambda: [
        "hinglish", "hindi", "english", "tamil", "telugu",
    ])
    
    # Prompts
    system_prompt: str = ""
    use_few_shot: bool = True
    max_few_shot_examples: int = 3


@dataclass
class CommentaryPrompt:
    """Structured commentary prompt"""
    # Event info
    event_type: str = ""
    event_description: str = ""
    
    # Participants
    player_name: str = ""
    team_name: str = ""
    opponent_name: str = ""
    opponent_team: str = ""
    
    # Context
    weapon: str = ""
    damage: int = 0
    headshot: bool = False
    distance: float = 0.0
    zone_phase: int = 0
    teams_alive: int = 16
    
    # Style
    style: CommentaryStyle = CommentaryStyle.HINGLISH
    tone: CommentaryTone = CommentaryTone.EXCITED
    language: str = "hinglish"
    
    # Historical context
    player_stats: str = ""
    team_stats: str = ""
    rivalry_context: str = ""
    storyline: str = ""
    
    # Recent events
    recent_events: List[str] = field(default_factory=list)
    
    def to_prompt_text(self) -> str:
        """Convert to prompt text"""
        parts = []
        
        parts.append(f"Event: {self.event_description}")
        
        if self.player_name:
            parts.append(f"Player: {self.player_name} ({self.team_name})")
        
        if self.opponent_name:
            parts.append(f"Opponent: {self.opponent_name} ({self.opponent_team})")
        
        if self.weapon:
            parts.append(f"Weapon: {self.weapon}")
        
        if self.damage > 0:
            parts.append(f"Damage: {self.damage}")
        
        if self.headshot:
            parts.append("HEADSHOT!")
        
        if self.distance > 0:
            parts.append(f"Distance: {self.distance:.0f}m")
        
        parts.append(f"Zone Phase: {self.zone_phase}")
        parts.append(f"Teams Alive: {self.teams_alive}")
        
        if self.player_stats:
            parts.append(f"Player Stats: {self.player_stats}")
        
        if self.rivalry_context:
            parts.append(f"Rivalry: {self.rivalry_context}")
        
        if self.storyline:
            parts.append(f"Storyline: {self.storyline}")
        
        return "\n".join(parts)


@dataclass
class GeneratedCommentary:
    """Generated commentary result"""
    text: str
    language: str
    style: CommentaryStyle
    tone: CommentaryTone
    
    # Metadata
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    
    # Timing
    generation_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    
    # Quality
    confidence: float = 0.8
    novelty_score: float = 0.5
    
    # Source
    model_name: str = ""
    provider: str = ""
    
    # Caching
    cache_key: str = ""
    is_cached: bool = False


class LLMChain:
    """
    LangChain-based pipeline for commentary generation.
    
    Features:
    - Multi-provider support (OpenAI, Anthropic, local LLMs)
    - Context-aware prompt construction
    - Few-shot example injection
    - Response caching with deduplication
    - Rate limiting
    - Retry logic with exponential backoff
    - Novelty detection to avoid repetition
    """
    
    def __init__(self, config: Optional[ChainConfig] = None):
        self.config = config or ChainConfig()
        
        # LLM client
        self._client: Optional[Any] = None
        
        # Cache
        self._cache: Dict[str, Tuple[float, GeneratedCommentary]] = {}
        
        # Rate limiting
        self._request_times: deque = deque(maxlen=100)
        self._token_usage: deque = deque(maxlen=100)
        
        # Recent commentary for novelty checking
        self._recent_commentary: deque = deque(maxlen=self.config.max_recent_commentary)
        
        # Statistics
        self._total_requests = 0
        self._total_tokens = 0
        self._total_cache_hits = 0
        self._total_errors = 0
        self._total_generation_time = 0.0
        
        self._log = logger.bind(component="llm_chain")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LLM CLIENT INITIALIZATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def initialize_client(self) -> bool:
        """Initialize LLM client based on provider"""
        
        try:
            if self.config.provider == LLMProvider.OPENAI:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.config.api_key,
                    base_url=self.config.api_base,
                )
                
            elif self.config.provider == LLMProvider.ANTHROPIC:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.config.api_key)
                
            elif self.config.provider in [LLMProvider.LOCAL_LLAMA, LLMProvider.LOCAL_MISTRAL]:
                from transformers import AutoModelForCausalLM, AutoTokenizer
                import torch
                
                model_name = self.config.model_name
                self._client = {
                    'model': AutoModelForCausalLM.from_pretrained(
                        model_name,
                        torch_dtype=torch.float16,
                        device_map="auto",
                    ),
                    'tokenizer': AutoTokenizer.from_pretrained(model_name),
                }
                
            elif self.config.provider == LLMProvider.VLLM:
                from vllm import LLM
                self._client = LLM(model=self.config.model_name)
                
            else:
                self._log.error(f"Unknown provider: {self.config.provider}")
                return False
            
            self._log.info(f"LLM client initialized: {self.config.provider.value} - {self.config.model_name}")
            return True
            
        except ImportError as e:
            self._log.error(f"Failed to import LLM library: {e}")
            return False
        except Exception as e:
            self._log.error(f"Failed to initialize LLM client: {e}")
            return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMENTARY GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def generate(
        self,
        prompt: CommentaryPrompt,
        style: Optional[CommentaryStyle] = None,
        tone: Optional[CommentaryTone] = None,
        language: Optional[str] = None,
        use_cache: bool = True,
    ) -> Optional[GeneratedCommentary]:
        """
        Generate commentary for an event.
        
        Args:
            prompt: Structured commentary prompt
            style: Override commentary style
            tone: Override emotional tone
            language: Override language
            use_cache: Whether to use response caching
            
        Returns:
            GeneratedCommentary or None if generation failed
        """
        
        if not self._client:
            self._log.error("LLM client not initialized")
            return None
        
        # Apply overrides
        prompt.style = style or prompt.style
        prompt.tone = tone or prompt.tone
        prompt.language = language or prompt.language
        
        # Build full prompt
        full_prompt = self._build_prompt(prompt)
        
        # Check cache
        cache_key = ""
        if use_cache and self.config.enable_cache:
            cache_key = self._get_cache_key(full_prompt)
            cached = self._get_from_cache(cache_key)
            if cached:
                self._total_cache_hits += 1
                return cached
        
        # Check rate limit
        if not self._check_rate_limit():
            self._log.warning("Rate limit reached - skipping generation")
            return None
        
        # Generate with retry
        start_time = time.time()
        
        for attempt in range(self.config.max_retries):
            try:
                text = await self._call_llm(full_prompt)
                
                if text and len(text) > 5:
                    # Create result
                    result = GeneratedCommentary(
                        text=text,
                        language=prompt.language,
                        style=prompt.style,
                        tone=prompt.tone,
                        generation_time_ms=(time.time() - start_time) * 1000,
                        model_name=self.config.model_name,
                        provider=self.config.provider.value,
                        cache_key=cache_key,
                    )
                    
                    # Check novelty
                    result.novelty_score = self._check_novelty(text)
                    
                    # Cache result
                    if cache_key:
                        self._add_to_cache(cache_key, result)
                    
                    # Update recent commentary
                    self._recent_commentary.append(text)
                    
                    # Update statistics
                    self._total_requests += 1
                    self._total_generation_time += result.generation_time_ms
                    
                    # Track request for rate limiting
                    self._request_times.append(time.time())
                    
                    return result
                
            except Exception as e:
                self._log.error(f"Generation attempt {attempt + 1} failed: {e}")
                self._total_errors += 1
                
                if attempt < self.config.max_retries - 1:
                    await self._async_sleep(self.config.retry_delay * (2 ** attempt))
        
        return None
    
    def _build_prompt(self, prompt: CommentaryPrompt) -> str:
        """Build complete prompt with system message and few-shot examples"""
        
        parts = []
        
        # System prompt
        if self.config.system_prompt:
            parts.append(self.config.system_prompt)
        else:
            parts.append(self._get_default_system_prompt(prompt))
        
        # Style and tone instruction
        parts.append(f"\nStyle: {prompt.style.value}")
        parts.append(f"Tone: {prompt.tone.value}")
        parts.append(f"Language: {prompt.language}")
        
        # Few-shot examples
        if self.config.use_few_shot:
            examples = self._get_few_shot_examples(prompt.style, prompt.tone)
            if examples:
                parts.append("\nExamples of good commentary:")
                for ex in examples[:self.config.max_few_shot_examples]:
                    parts.append(f"Input: {ex['input']}")
                    parts.append(f"Output: {ex['output']}")
        
        # Event context
        parts.append("\nEvent to commentate:")
        parts.append(prompt.to_prompt_text())
        
        # Recent commentary (for context)
        if self._recent_commentary:
            parts.append("\nRecent commentary (avoid repeating):")
            for recent in list(self._recent_commentary)[-3:]:
                parts.append(f"- {recent}")
        
        # Instruction
        parts.append(f"\nGenerate ONE line of {prompt.language} {prompt.style.value} commentary.")
        parts.append("Keep it under 20 words. Be natural and exciting.")
        parts.append("Do NOT repeat recent commentary. Be creative.")
        
        return "\n".join(parts)
    
    def _get_default_system_prompt(self, prompt: CommentaryPrompt) -> str:
        """Get default system prompt"""
        
        return f"""You are a professional BGMI (Battlegrounds Mobile India) esports caster.
You provide exciting, authentic commentary in {prompt.language} style.
Your commentary should sound like a real esports broadcast - energetic, knowledgeable, and engaging.

Guidelines:
- Use natural {prompt.language} language
- Match the energy to the moment
- Include relevant player/team context when available
- Be concise but impactful (one line)
- Sound like a human caster, not a robot
- Use gaming/esports terminology naturally
- Don't overuse catchphrases"""
    
    def _get_few_shot_examples(
        self,
        style: CommentaryStyle,
        tone: CommentaryTone,
    ) -> List[Dict[str, str]]:
        """Get few-shot examples for style and tone"""
        
        # Default examples (would be loaded from few_shot_examples.py)
        examples = {
            CommentaryStyle.HINGLISH: [
                {
                    "input": "Kill with M416, close range",
                    "output": "BHAI! Kya laser beam spray tha! Ekdum perfect M416 control!",
                },
                {
                    "input": "Clutch 1v3 situation",
                    "output": "ARE BHAI! Yeh kya dekh liya! 1v3 clutch incoming! Tension at peak!",
                },
                {
                    "input": "Squad wipe in final circle",
                    "output": "GAYE! Poori squad wipe! Final circle mein absolute destruction!",
                },
            ],
            CommentaryStyle.HYPE: [
                {
                    "input": "Multi-kill moment",
                    "output": "OH MY GOD! THREE KILLS IN SECONDS! ABSOLUTE DESTRUCTION!",
                },
                {
                    "input": "Long range headshot",
                    "output": "WHAT A SHOT! FROM 300 METERS! THAT'S INSANE ACCURACY!",
                },
            ],
            CommentaryStyle.ANALYTICAL: [
                {
                    "input": "Rotation decision",
                    "output": "Smart rotation - reading the zone perfectly and taking optimal position.",
                },
            ],
        }
        
        return examples.get(style, examples[CommentaryStyle.HINGLISH])
    
    async def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM with the constructed prompt"""
        
        try:
            if self.config.provider == LLMProvider.OPENAI:
                import openai
                
                response = await openai.chat.completions.create(
                    model=self.config.model_name,
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                    frequency_penalty=self.config.frequency_penalty,
                    presence_penalty=self.config.presence_penalty,
                    stop=self.config.stop_sequences,
                )
                
                self._total_tokens += response.usage.total_tokens
                self._token_usage.append((time.time(), response.usage.total_tokens))
                
                return response.choices[0].message.content.strip()
                
            elif self.config.provider == LLMProvider.ANTHROPIC:
                
                # Anthropic API call
                pass
                
            elif self.config.provider in [LLMProvider.LOCAL_LLAMA, LLMProvider.LOCAL_MISTRAL]:
                import torch
                
                tokenizer = self._client['tokenizer']
                model = self._client['model']
                
                inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
                
                with torch.no_grad():
                    outputs = model.generate(
                        **inputs,
                        max_new_tokens=self.config.max_tokens,
                        temperature=self.config.temperature,
                        top_p=self.config.top_p,
                        do_sample=True,
                    )
                
                text = tokenizer.decode(outputs[0], skip_special_tokens=True)
                # Remove the input prompt from output
                text = text[len(prompt):].strip()
                
                return text
                
            elif self.config.provider == LLMProvider.VLLM:
                outputs = self._client.generate(
                    [prompt],
                    sampling_params={
                        "max_tokens": self.config.max_tokens,
                        "temperature": self.config.temperature,
                        "top_p": self.config.top_p,
                    },
                )
                return outputs[0].outputs[0].text.strip()
            
        except Exception as e:
            self._log.error(f"LLM call error: {e}")
            raise
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CACHE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_cache_key(self, prompt: str) -> str:
        """Generate cache key from prompt"""
        return hashlib.md5(prompt.encode()).hexdigest()
    
    def _get_from_cache(self, cache_key: str) -> Optional[GeneratedCommentary]:
        """Get from cache if valid"""
        if cache_key in self._cache:
            timestamp, result = self._cache[cache_key]
            if time.time() - timestamp < self.config.cache_ttl:
                result.is_cached = True
                return result
            else:
                del self._cache[cache_key]
        return None
    
    def _add_to_cache(self, cache_key: str, result: GeneratedCommentary) -> None:
        """Add to cache with TTL"""
        self._cache[cache_key] = (time.time(), result)
        
        # Limit cache size
        if len(self._cache) > self.config.cache_max_size:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            del self._cache[oldest]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NOVELTY & RATE LIMITING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _check_novelty(self, text: str) -> float:
        """Check how novel the text is compared to recent commentary"""
        
        if not self._recent_commentary:
            return 1.0
        
        # Simple word overlap check
        words = set(text.lower().split())
        
        max_overlap = 0.0
        for recent in list(self._recent_commentary)[-5:]:
            recent_words = set(recent.lower().split())
            if len(words) > 0:
                overlap = len(words & recent_words) / len(words)
                max_overlap = max(max_overlap, overlap)
        
        return 1.0 - max_overlap
    
    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits"""
        
        current_time = time.time()
        
        # Clean old entries
        self._request_times = deque(
            [t for t in self._request_times if current_time - t < 60],
            maxlen=100,
        )
        self._token_usage = deque(
            [(t, tok) for t, tok in self._token_usage if current_time - t < 60],
            maxlen=100,
        )
        
        # Check request rate
        if len(self._request_times) >= self.config.max_requests_per_minute:
            return False
        
        # Check token rate
        total_tokens = sum(tok for _, tok in self._token_usage)
        if total_tokens >= self.config.max_tokens_per_minute:
            return False
        
        return True
    
    async def _async_sleep(self, seconds: float) -> None:
        """Async sleep"""
        import asyncio
        await asyncio.sleep(seconds)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get LLM chain statistics"""
        return {
            "provider": self.config.provider.value,
            "model": self.config.model_name,
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "cache_hits": self._total_cache_hits,
            "cache_size": len(self._cache),
            "errors": self._total_errors,
            "avg_generation_time_ms": round(
                self._total_generation_time / max(1, self._total_requests), 2
            ),
            "rate_limited": not self._check_rate_limit(),
        }
    
    def clear_cache(self) -> None:
        """Clear response cache"""
        self._cache.clear()
        self._log.info("Commentary cache cleared")
    
    def reset(self) -> None:
        """Reset LLM chain"""
        self._cache.clear()
        self._recent_commentary.clear()
        self._request_times.clear()
        self._token_usage.clear()
        self._total_requests = 0
        self._total_tokens = 0
        self._total_cache_hits = 0
        self._total_errors = 0
        self._total_generation_time = 0.0
        self._log.info("LLM chain reset")