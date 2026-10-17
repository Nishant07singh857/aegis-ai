# backend/tests/unit/test_agents/test_commentary_agent.py
"""
Unit tests for Commentary Agent.
Tests script generation, emotion engine, hype detection,
voice synthesis, caster memory, and language management.
"""

import pytest
import time
from unittest.mock import AsyncMock, Mock

from app.agents.base_agent import AgentConfig
from app.agents.commentary_agents.script_generator import (
    ScriptGenerator, CommentaryStyle, CommentaryTone, CommentaryTrigger,
    CommentaryLine,
)
from app.agents.commentary_agents.emotion_engine import (
    EmotionEngine, EmotionalState,
)
from app.agents.commentary_agents.hype_detector import (
    HypeDetector, HypeMoment, HypeLevel, HypeCategory,
)
from app.agents.commentary_agents.voice_synthesizer import (
    VoiceSynthesizer, VoiceStyle, SynthesizedAudio,
)
from app.agents.commentary_agents.caster_memory import (
    CasterMemory,
)
from app.agents.commentary_agents.language_manager import (
    LanguageManager, Language,
)


def _make_bus():
    """Create a mock event bus for agent construction."""
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="test-event-id")
    bus.subscribe = Mock(return_value="subscriber-id")
    bus.unsubscribe = Mock(return_value=True)
    return bus


def _make_config(agent_id: str, agent_type: str) -> AgentConfig:
    """Create a minimal agent config for tests."""
    return AgentConfig(agent_id=agent_id, agent_type=agent_type)


@pytest.mark.unit
@pytest.mark.agents
class TestScriptGenerator:
    """Test ScriptGenerator functionality"""
    
    @pytest.fixture
    def generator(self):
        """Create ScriptGenerator instance"""
        return ScriptGenerator(
            config=_make_config("script_generator", "script_generator"),
            event_bus=_make_bus(),
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, generator):
        """Test generator initializes with correct defaults"""
        assert generator.active_style == CommentaryStyle.HINGLISH
        assert generator.active_tone == CommentaryTone.CASUAL
        assert len(generator.prompt_templates) > 0
        assert len(generator.few_shot_examples) > 0
    
    def test_prompt_templates_loaded(self, generator):
        """Test all prompt templates are loaded"""
        expected_templates = [
            "kill", "multi_kill", "team_wipe", "clutch_start",
            "clutch_win", "fight_intense", "final_circle", "filler",
        ]
        
        for template in expected_templates:
            assert template in generator.prompt_templates, f"Missing template: {template}"
    
    def test_few_shot_examples_loaded(self, generator):
        """Test few-shot examples are loaded"""
        expected_styles = ["hype", "analytical", "hinglish", "hindi"]
        
        for style in expected_styles:
            assert style in generator.few_shot_examples, f"Missing examples for: {style}"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMENTARY GENERATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_generate_kill_commentary(self, generator):
        """Test generating commentary for a kill event"""
        context = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "victim_name": "Jonathan",
            "victim_team": "GodLike",
            "weapon": "M416",
            "headshot": True,
            "distance": 50,
            "zone_phase": 3,
            "teams_alive": 12,
            "player_kills": 5,
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.KILL,
            context=context,
            style=CommentaryStyle.HINGLISH,
            tone=CommentaryTone.EXCITED,
        )
        
        assert line is not None
        assert line.text is not None
        assert len(line.text) > 5
        assert line.style == CommentaryStyle.HINGLISH
        assert line.tone == CommentaryTone.EXCITED
        assert line.priority > 0
    
    @pytest.mark.asyncio
    async def test_generate_clutch_start_commentary(self, generator):
        """Test generating commentary for clutch start"""
        context = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "enemy_count": 4,
            "probability": "8%",
            "hp": 20,
            "armor": 50,
            "weapons": "M416, AWM",
            "difficulty": "extreme",
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.CLUTCH_START,
            context=context,
            style=CommentaryStyle.HINGLISH,
            tone=CommentaryTone.TENSE,
        )
        
        assert line is not None
        assert line.text is not None
        assert line.priority >= 90  # Clutch has very high priority
    
    @pytest.mark.asyncio
    async def test_generate_clutch_win_commentary(self, generator):
        """Test generating commentary for clutch win"""
        context = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "clutch_type": "1v4",
            "probability": "5%",
            "enemies_killed": 4,
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.CLUTCH_WIN,
            context=context,
            style=CommentaryStyle.HYPE,
            tone=CommentaryTone.CELEBRATORY,
        )
        
        assert line is not None
        assert line.priority == 100  # Clutch win is highest priority
    
    @pytest.mark.asyncio
    async def test_generate_multi_kill_commentary(self, generator):
        """Test generating commentary for multi-kill"""
        context = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "kill_count": 3,
            "time_span": 4.5,
            "weapons": "M416, AKM",
            "intensity": "extreme",
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.MULTI_KILL,
            context=context,
            style=CommentaryStyle.HYPE,
        )
        
        assert line is not None
    
    @pytest.mark.asyncio
    async def test_generate_team_wipe_commentary(self, generator):
        """Test generating commentary for team wipe"""
        context = {
            "team_name": "GodLike",
            "eliminator_team": "Team Soul",
            "placement": 5,
            "team_kills": 8,
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.TEAM_WIPE,
            context=context,
            style=CommentaryStyle.HINGLISH,
            tone=CommentaryTone.DRAMATIC,
        )
        
        assert line is not None
    
    @pytest.mark.asyncio
    async def test_generate_final_circle_commentary(self, generator):
        """Test generating commentary for final circle"""
        context = {
            "teams_alive": 3,
            "team_list": "Team Soul, GodLike, Revenant",
            "zone_time": 15,
            "match_state": "final_circle",
        }
        
        line = await generator.generate_commentary(
            trigger=CommentaryTrigger.FINAL_CIRCLE,
            context=context,
            style=CommentaryStyle.CINEMATIC,
            tone=CommentaryTone.TENSE,
        )
        
        assert line is not None
    
    @pytest.mark.asyncio
    async def test_generate_filler_commentary(self, generator):
        """Test generating filler commentary during silence"""
        context = {
            "match_state": "mid_game",
            "teams_alive": 10,
            "zone_phase": 3,
            "notable_info": "Teams rotating to zone",
        }
        
        line = await generator.generate_filler(context)
        
        assert line is not None
        assert line.text is not None
    
    @pytest.mark.asyncio
    async def test_generate_match_start(self, generator):
        """Test generating match opening commentary"""
        teams = [
            {"team_id": "t1", "team_name": "Team Soul"},
            {"team_id": "t2", "team_name": "GodLike"},
        ]
        
        lines = await generator.generate_match_start(
            teams=teams,
            map_name="Erangel",
            tournament_name="AEGIS Invitational",
        )
        
        assert len(lines) > 0
        for line in lines:
            assert line.text is not None
    
    @pytest.mark.asyncio
    async def test_generate_match_end(self, generator):
        """Test generating match closing commentary"""
        lines = await generator.generate_match_end(
            winner_team="Team Soul",
            winner_players=["ScoutOP", "Mortal", "Viper", "Goblin"],
            match_stats={"total_kills": 35, "mvp": "ScoutOP"},
        )
        
        assert len(lines) > 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STYLE MANAGEMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_set_style(self, generator):
        """Test changing commentary style"""
        generator.set_style(CommentaryStyle.HINDI)
        assert generator.active_style == CommentaryStyle.HINDI
        
        generator.set_style(CommentaryStyle.ANALYTICAL)
        assert generator.active_style == CommentaryStyle.ANALYTICAL
    
    def test_set_tone(self, generator):
        """Test changing commentary tone"""
        generator.set_tone(CommentaryTone.DRAMATIC)
        assert generator.active_tone == CommentaryTone.DRAMATIC
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BUFFER MANAGEMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_buffer_add_line(self, generator):
        """Test adding line to commentary buffer"""
        line = CommentaryLine(
            text="Test commentary line",
            priority=50,
        )
        
        generator._add_to_buffer(line)
        
        assert len(generator.buffer.lines) == 1
        assert generator.buffer.total_lines_generated == 1
    
    def test_get_next_line(self, generator):
        """Test getting next line from buffer"""
        line = CommentaryLine(
            text="First line",
            duration_estimate=0.1,  # Very short duration
            priority=50,
        )
        
        generator._add_to_buffer(line)
        
        # First call should return the line
        result = generator.get_next_line()
        assert result is not None
        assert result.text == "First line"
    
    def test_needs_filler(self, generator):
        """Test filler detection when silent too long"""
        generator.buffer.last_spoken_time = time.time() - 10  # 10 seconds ago
        generator.buffer.max_silence = 8.0
        
        assert generator.needs_filler() is True
    
    def test_silence_duration(self, generator):
        """Test getting silence duration"""
        generator.buffer.last_spoken_time = time.time() - 5
        
        duration = generator.get_silence_duration()
        
        assert duration >= 4.5  # Approximately 5 seconds
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NOVELTY & QUALITY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_novelty_check_unique(self, generator):
        """Test novelty check for unique text"""
        text = "BHAI! Kya incredible play tha!"
        
        score = generator._check_novelty(text)
        
        assert score == 1.0  # Completely novel
    
    def test_novelty_check_repetitive(self, generator):
        """Test novelty check for repetitive text"""
        generator._recent_commentary.append("BHAI! Kya incredible play tha!")
        
        text = "BHAI! Kya incredible play tha!"  # Same text
        
        score = generator._check_novelty(text)
        
        assert score == 0.0  # Completely repetitive
    
    def test_estimate_duration(self, generator):
        """Test duration estimation"""
        text = "BHAI! Kya incredible play tha! Absolutely brilliant!"
        duration = generator._estimate_duration(text)
        
        assert duration >= 2.0  # Minimum 2 seconds
        assert duration < 10.0  # Should not be too long
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRIGGER PRIORITY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_trigger_priorities(self, generator):
        """Test that different triggers have appropriate priorities"""
        priorities = {
            CommentaryTrigger.CLUTCH_WIN: 100,
            CommentaryTrigger.CLUTCH_START: 95,
            CommentaryTrigger.TEAM_WIPE: 90,
            CommentaryTrigger.MULTI_KILL: 85,
            CommentaryTrigger.FIGHT_INTENSE: 80,
            CommentaryTrigger.KILL: 60,
            CommentaryTrigger.INTERESTING_MOMENT: 20,
        }
        
        for trigger, expected_priority in priorities.items():
            priority = generator._get_trigger_priority(trigger)
            assert priority == expected_priority, f"Wrong priority for {trigger.value}"
    
    def test_cooldown_respected(self, generator):
        """Test that trigger cooldown is respected"""
        generator.last_trigger_time["kill"] = time.time()  # Just triggered
        
        assert generator._is_in_cooldown("kill") is True
    
    def test_cooldown_expired(self, generator):
        """Test that cooldown expires"""
        generator.last_trigger_time["kill"] = time.time() - 10  # 10 seconds ago
        
        assert generator._is_in_cooldown("kill") is False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_stats(self, generator):
        """Test getting script generator statistics"""
        stats = generator.get_stats()
        
        assert "total_lines_generated" in stats
        assert "buffer_size" in stats
        assert "active_style" in stats
        assert "silence_duration" in stats
        assert "needs_filler" in stats
        assert "templates_loaded" in stats
    
    def test_reset(self, generator):
        """Test resetting script generator"""
        generator.buffer.total_lines_generated = 100
        generator.last_trigger_time["kill"] = time.time()
        
        generator.reset()
        
        assert generator.buffer.total_lines_generated == 0
        assert len(generator.last_trigger_time) == 0


@pytest.mark.unit
@pytest.mark.agents
class TestEmotionEngine:
    """Test EmotionEngine functionality"""
    
    @pytest.fixture
    def engine(self):
        """Create EmotionEngine instance"""
        return EmotionEngine(
            config=_make_config("emotion_engine", "emotion_engine"),
            event_bus=_make_bus(),
        )
    
    def test_initialization(self, engine):
        """Test engine initializes with calm state"""
        assert engine.profile.state == EmotionalState.CALM
        assert engine.profile.energy_level == 30.0
        assert engine.profile.tension_level == 10.0
    
    def test_update_energy_increases_with_fight(self, engine):
        """Test energy increases with active fight"""
        context = {
            "phase": "mid",
            "teams_alive": 8,
            "zone_phase": 4,
            "active_fights": 2,
            "has_clutch": False,
            "recent_kills": 3,
        }
        
        events = [
            {"type": "fight_intense"},
            {"type": "kill"},
            {"type": "kill"},
        ]
        
        engine.update(dt=0.5, match_context=context, active_events=events)
        
        assert engine.profile.energy_level > 30.0  # Should increase
    
    def test_update_tension_increases_with_clutch(self, engine):
        """Test tension increases with clutch situation"""
        context = {
            "phase": "late",
            "teams_alive": 4,
            "zone_phase": 6,
            "has_clutch": True,
        }
        
        events = [
            {"type": "clutch_start"},
        ]
        
        engine.update(dt=0.5, match_context=context, active_events=events)
        
        assert engine.profile.tension_level > 10.0  # Should increase
    
    def test_energy_decays_naturally(self, engine):
        """Test energy decays over time without events"""
        engine.profile.energy_level = 80.0
        
        engine.update(dt=5.0, match_context={"phase": "mid"}, active_events=[])
        
        assert engine.profile.energy_level < 80.0  # Should decay
    
    def test_transition_to_excited(self, engine):
        """Test state transition to excited"""
        engine.profile.energy_level = 70.0
        engine.profile.tension_level = 30.0
        
        new_state = engine._determine_state(70.0, 30.0)
        
        assert new_state == EmotionalState.EXCITED
    
    def test_transition_to_tense(self, engine):
        """Test state transition to tense"""
        new_state = engine._determine_state(40.0, 80.0)
        
        assert new_state == EmotionalState.TENSE
    
    def test_transition_to_hyped(self, engine):
        """Test state transition to hyped"""
        new_state = engine._determine_state(90.0, 30.0)
        
        assert new_state == EmotionalState.HYPED
    
    def test_transition_to_dramatic(self, engine):
        """Test state transition to dramatic"""
        new_state = engine._determine_state(85.0, 80.0)
        
        assert new_state == EmotionalState.DRAMATIC
    
    def test_on_kill_boosts_energy(self, engine):
        """Test kill event boosts energy"""
        initial_energy = engine.profile.energy_level
        
        engine.on_kill(is_multi_kill=False)
        
        assert engine.profile.energy_level > initial_energy
    
    def test_on_multi_kill_boosts_more(self, engine):
        """Test multi-kill boosts energy more than single kill"""
        energy_before = engine.profile.energy_level
        
        engine.on_kill(is_multi_kill=True)
        
        energy_after_single = engine.profile.energy_level
        
        engine.profile.energy_level = energy_before
        engine.on_kill(is_multi_kill=True)
        
        energy_after_multi = engine.profile.energy_level
        
        assert energy_after_multi >= energy_after_single
    
    def test_on_clutch_start(self, engine):
        """Test clutch start event"""
        engine.on_clutch_start()
        
        assert engine.profile.tension_level > 50
        assert engine.profile.energy_level > 40
    
    def test_on_clutch_win_maximum_excitement(self, engine):
        """Test clutch win triggers celebratory state"""
        engine.on_clutch_win()
        
        assert engine.profile.state == EmotionalState.CELEBRATORY
        assert engine.profile.energy_level > 50
        assert engine.profile.tension_level < 40
    
    def test_get_current_profile(self, engine):
        """Test getting current emotional profile"""
        profile = engine.get_current_profile()
        
        assert "state" in profile
        assert "intensity" in profile
        assert "energy" in profile
        assert "tension" in profile
        assert "speaking_rate" in profile
        assert "pitch_modifier" in profile
    
    def test_get_recommended_tone(self, engine):
        """Test getting recommended commentary tone"""
        engine.profile.state = EmotionalState.EXCITED
        tone = engine.get_recommended_tone()
        assert tone == "excited"
        
        engine.profile.state = EmotionalState.TENSE
        tone = engine.get_recommended_tone()
        assert tone == "tense"


@pytest.mark.unit
@pytest.mark.agents
class TestHypeDetector:
    """Test HypeDetector functionality"""
    
    @pytest.fixture
    def detector(self):
        """Create HypeDetector instance"""
        return HypeDetector(
            config=_make_config("hype_detector", "hype_detector"),
            event_bus=_make_bus(),
        )
    
    def test_initialization(self, detector):
        """Test detector initializes correctly"""
        assert detector.history.hype_count == 0
        assert detector.history.peak_moment is None
    
    def test_detect_clutch_hype(self, detector):
        """Test detecting clutch as hype moment"""
        data = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "enemy_count": 4,
            "probability": 0.08,
        }
        
        moment = detector._check_clutch_hype(data, {})
        
        assert moment is not None
        assert moment.category == HypeCategory.CLUTCH
        assert moment.hype_score > 70
    
    def test_detect_clutch_win_legendary(self, detector):
        """Test detecting clutch win as legendary"""
        data = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "enemies_killed": 4,
            "probability": 0.05,
        }
        
        moment = detector._check_clutch_win_hype(data, {})
        
        assert moment is not None
        assert moment.level == HypeLevel.LEGENDARY
        assert moment.hype_score >= 95
        assert moment.should_highlight is True
        assert moment.replay_priority == 100
    
    def test_detect_multi_kill_hype(self, detector):
        """Test detecting multi-kill as hype"""
        data = {
            "kill_count": 4,
            "time_span": 5.0,
            "player_name": "ScoutOP",
        }
        
        moment = detector._check_multi_kill_hype(data, {})
        
        assert moment is not None
        assert moment.level == HypeLevel.HYPE
    
    def test_detect_team_wipe_hype(self, detector):
        """Test detecting team wipe hype"""
        data = {
            "team_name": "GodLike",
            "eliminator": "Team Soul",
            "placement": 3,
        }
        
        moment = detector._check_team_wipe_hype(data, {})
        
        assert moment is not None
        assert moment.category == HypeCategory.TEAM_WIPE
    
    def test_detect_grenade_hype(self, detector):
        """Test detecting grenade kill hype"""
        data = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "victims": 3,
        }
        
        moment = detector._check_grenade_hype(data, {})
        
        assert moment is not None
        assert moment.level == HypeLevel.HYPE
    
    def test_detect_long_range_hype(self, detector):
        """Test detecting long range kill hype"""
        data = {
            "player_name": "ScoutOP",
            "team_name": "Team Soul",
            "distance": 350,
        }
        
        moment = detector._check_long_range_hype(data, {})
        
        assert moment is not None
        assert moment.level == HypeLevel.HYPE
    
    def test_get_current_hype_level(self, detector):
        """Test getting current hype level"""
        # Add a hype moment
        moment = HypeMoment(
            moment_id="test_moment",
            category=HypeCategory.CLUTCH,
            level=HypeLevel.HYPE,
            hype_score=85.0,
            description="Test clutch",
        )
        detector._process_hype_moment(moment)
        
        level, score = detector.get_current_hype_level()
        
        assert level in [HypeLevel.HYPE, HypeLevel.MILD]
        assert score > 0
    
    def test_get_recommended_energy(self, detector):
        """Test getting recommended energy level"""
        moment = HypeMoment(
            moment_id="test_moment",
            category=HypeCategory.CLUTCH_WIN,
            level=HypeLevel.LEGENDARY,
            hype_score=95.0,
            description="Test legendary",
        )
        detector._process_hype_moment(moment)
        
        energy = detector.get_recommended_energy()
        
        assert energy > 0
    
    def test_get_peak_moment(self, detector):
        """Test getting peak moment"""
        moment = HypeMoment(
            moment_id="peak_moment",
            category=HypeCategory.CLUTCH_WIN,
            level=HypeLevel.LEGENDARY,
            hype_score=98.0,
            description="Peak clutch",
        )
        detector._process_hype_moment(moment)
        
        peak = detector.get_peak_moment()
        
        assert peak is not None
        assert peak["level"] == "legendary"
    
    def test_get_recent_hype(self, detector):
        """Test getting recent hype moments"""
        for i in range(3):
            moment = HypeMoment(
                moment_id=f"moment_{i}",
                category=HypeCategory.KILL_HYPE,
                level=HypeLevel.EXCITING,
                hype_score=70.0,
                description=f"Test moment {i}",
            )
            detector._process_hype_moment(moment)
        
        recent = detector.get_recent_hype(limit=3)
        
        assert len(recent) == 3


@pytest.mark.unit
@pytest.mark.agents
class TestVoiceSynthesizer:
    """Test VoiceSynthesizer functionality"""
    
    @pytest.fixture
    def synthesizer(self):
        """Create VoiceSynthesizer instance"""
        return VoiceSynthesizer(
            config=_make_config("voice_synthesizer", "voice_synthesizer"),
            event_bus=_make_bus(),
        )
    
    def test_initialization(self, synthesizer):
        """Test synthesizer initializes with voice profiles"""
        assert len(synthesizer.profiles) > 0
        assert synthesizer.active_profile_id != ""
    
    def test_profiles_created(self, synthesizer):
        """Test default voice profiles are created"""
        assert "hinglish_caster" in synthesizer.profiles
        assert "hindi_caster_1" in synthesizer.profiles
        assert "english_analyst" in synthesizer.profiles
    
    @pytest.mark.asyncio
    async def test_synthesize_text(self, synthesizer):
        """Test synthesizing text to speech"""
        audio = await synthesizer.synthesize(
            text="BHAI! Kya incredible play tha!",
            profile_id="hinglish_caster",
        )
        
        assert audio is not None
        assert audio.text == "BHAI! Kya incredible play tha!"
        assert audio.duration_seconds > 0
    
    @pytest.mark.asyncio
    async def test_synthesize_with_emotion(self, synthesizer):
        """Test synthesizing with emotion modulation"""
        emotion_params = {
            "energy": 85.0,
            "tension": 30.0,
        }
        
        audio = await synthesizer.synthesize(
            text="OH MY GOD! WHAT A PLAY!",
            style=VoiceStyle.EXCITED,
            emotion_params=emotion_params,
        )
        
        assert audio is not None
    
    @pytest.mark.asyncio
    async def test_synthesize_batch(self, synthesizer):
        """Test batch synthesis"""
        lines = [
            ("First line of commentary", "hinglish_caster", VoiceStyle.NORMAL),
            ("Second line of commentary", "hinglish_caster", VoiceStyle.EXCITED),
            ("Third line of commentary", "english_analyst", VoiceStyle.CALM),
        ]
        
        results = await synthesizer.synthesize_batch(lines)
        
        assert len(results) == 3
        for audio in results:
            assert audio is not None
    
    def test_set_active_profile(self, synthesizer):
        """Test changing active voice profile"""
        result = synthesizer.set_active_profile("hindi_caster_1")
        assert result is True
        assert synthesizer.active_profile_id == "hindi_caster_1"
        
        result = synthesizer.set_active_profile("non_existent")
        assert result is False
    
    def test_get_active_profile(self, synthesizer):
        """Test getting active profile"""
        profile = synthesizer.get_active_profile()
        assert profile is not None
        assert profile.profile_id == synthesizer.active_profile_id
    
    def test_clear_cache(self, synthesizer):
        """Test clearing audio cache"""
        synthesizer._add_to_cache("test_key", SynthesizedAudio(
            audio_id="test",
            text="test",
            duration_seconds=1.0,
        ))
        
        synthesizer.clear_cache()
        
        assert len(synthesizer.audio_cache) == 0
    
    def test_get_stats(self, synthesizer):
        """Test getting synthesizer statistics"""
        stats = synthesizer.get_stats()
        
        assert "total_synthesized" in stats
        assert "cache_hits" in stats
        assert "cache_size" in stats
        assert "active_profile" in stats
        assert "profiles" in stats


@pytest.mark.unit
@pytest.mark.agents
class TestCasterMemory:
    """Test CasterMemory functionality"""
    
    @pytest.fixture
    def memory(self):
        """Create CasterMemory instance"""
        return CasterMemory(
            config=_make_config("caster_memory", "caster_memory"),
            event_bus=_make_bus(),
        )
    
    def test_initialization(self, memory):
        """Test memory initializes empty"""
        assert len(memory.player_memory) == 0
        assert len(memory.team_memory) == 0
    
    def test_remember_player(self, memory):
        """Test remembering player performance"""
        memory.remember_player(
            player_id="player_001",
            player_name="ScoutOP",
            team_name="Team Soul",
            kills=8,
            damage=2500,
            was_clutch=True,
            was_mvp=True,
            playstyle="aggressive_fragger",
            weapon="M416",
        )
        
        assert "player_001" in memory.player_memory
        player = memory.player_memory["player_001"]
        assert player.total_kills == 8
        assert player.total_damage == 2500
        assert player.clutch_count == 1
        assert player.mvp_count == 1
        assert player.playstyle == "aggressive_fragger"
        assert player.signature_weapon == "M416"
    
    def test_remember_team(self, memory):
        """Test remembering team performance"""
        memory.remember_team(
            team_id="team_001",
            team_name="Team Soul",
            placement=1,
            was_win=True,
            playstyle="aggressive",
            rival="team_002",
        )
        
        assert "team_001" in memory.team_memory
        team = memory.team_memory["team_001"]
        assert team.total_wins == 1
        assert team.tournament_placement == 1
        assert team.playstyle == "aggressive"
        assert "team_002" in team.rivalry_teams
    
    def test_get_player_context(self, memory):
        """Test getting player context"""
        memory.remember_player("player_001", "ScoutOP", "Team Soul", kills=5, damage=1500)
        
        context = memory.get_player_context("player_001")
        
        assert context is not None
        assert context["name"] == "ScoutOP"
        assert context["team"] == "Team Soul"
        assert context["stats"]["total_kills"] == 5
    
    def test_get_team_context(self, memory):
        """Test getting team context"""
        memory.remember_team("team_001", "Team Soul", placement=1, was_win=True)
        
        context = memory.get_team_context("team_001")
        
        assert context is not None
        assert context["name"] == "Team Soul"
        assert context["stats"]["wins"] == 1
    
    def test_should_mention(self, memory):
        """Test mention cooldown logic"""
        # Should be able to mention (not mentioned recently)
        assert memory.should_mention("player_001") is True
        
        # Mark as mentioned
        memory.mark_mentioned("player_001")
        
        # Should not mention again immediately
        assert memory.should_mention("player_001") is False
    
    def test_get_head_to_head_context(self, memory):
        """Test getting head-to-head context"""
        memory.remember_team("team_001", "Team Soul", placement=1)
        memory.remember_team("team_002", "GodLike", placement=2)
        
        # Make them rivals
        memory.team_memory["team_001"].rivalry_teams.append("team_002")
        
        context = memory.get_head_to_head_context("team_001", "team_002")
        
        assert context is not None
        assert context["is_rivalry"] is True
        assert context["team_a"] == "Team Soul"
        assert context["team_b"] == "GodLike"
    
    def test_get_fun_fact(self, memory):
        """Test getting fun facts"""
        memory.remember_player("player_001", "ScoutOP", "Team Soul", kills=5)
        memory.player_memory["player_001"].clutch_count = 5
        
        fact = memory.get_fun_fact({"player_id": "player_001"})
        
        assert fact is not None
    
    def test_get_stats(self, memory):
        """Test getting memory statistics"""
        stats = memory.get_stats()
        
        assert "players_remembered" in stats
        assert "teams_remembered" in stats
        assert "active_storylines" in stats


@pytest.mark.unit
@pytest.mark.agents
class TestLanguageManager:
    """Test LanguageManager functionality"""
    
    @pytest.fixture
    def lang_manager(self):
        """Create LanguageManager instance"""
        return LanguageManager(
            config=_make_config("language_manager", "language_manager"),
            event_bus=_make_bus(),
        )
    
    def test_initialization(self, lang_manager):
        """Test language manager initializes with profiles"""
        assert len(lang_manager.profiles) > 0
        assert lang_manager.active_language == Language.HINGLISH
    
    def test_set_active_language(self, lang_manager):
        """Test changing active language"""
        result = lang_manager.set_active_language(Language.HINDI)
        assert result is True
        assert lang_manager.active_language == Language.HINDI
        
        result = lang_manager.set_active_language(Language.ENGLISH)
        assert result is True
        assert lang_manager.active_language == Language.ENGLISH
    
    def test_get_phrase_hinglish(self, lang_manager):
        """Test getting hinglish phrase"""
        phrase = lang_manager.get_phrase(
            template_key="kill",
            context={"player": "ScoutOP", "victim": "Jonathan", "weapon": "M416", "team": "Team Soul"},
            language=Language.HINGLISH,
        )
        
        assert phrase is not None
        assert len(phrase) > 5
    
    def test_get_phrase_hindi(self, lang_manager):
        """Test getting hindi phrase"""
        phrase = lang_manager.get_phrase(
            template_key="kill",
            context={"player": "ScoutOP", "victim": "Jonathan", "weapon": "M416"},
            language=Language.HINDI,
        )
        
        assert phrase is not None
    
    def test_get_phrase_english(self, lang_manager):
        """Test getting english phrase"""
        phrase = lang_manager.get_phrase(
            template_key="kill",
            context={"player": "ScoutOP", "victim": "Jonathan", "weapon": "M416", "team": "Team Soul"},
            language=Language.ENGLISH,
        )
        
        assert phrase is not None
    
    def test_get_exclamation(self, lang_manager):
        """Test getting random exclamation"""
        exclamation = lang_manager.get_exclamation(Language.HINGLISH)
        assert exclamation is not None
        assert len(exclamation) > 0
    
    def test_get_filler(self, lang_manager):
        """Test getting filler phrase"""
        filler = lang_manager.get_filler(Language.HINGLISH)
        assert filler is not None
    
    def test_get_transition(self, lang_manager):
        """Test getting transition phrase"""
        transition = lang_manager.get_transition(Language.HINGLISH)
        assert transition is not None
    
    def test_get_language_name(self, lang_manager):
        """Test getting language names"""
        assert lang_manager.get_language_name(Language.HINGLISH) == "Hinglish"
        assert lang_manager.get_language_name(Language.HINDI) == "Hindi"
        assert lang_manager.get_language_name(Language.ENGLISH) == "English"
    
    def test_translate(self, lang_manager):
        """Test translation (basic)"""
        text = "Amazing play!"
        translated = lang_manager.translate(text, Language.HINDI)
        
        assert translated is not None
    
    def test_get_stats(self, lang_manager):
        """Test getting language manager statistics"""
        stats = lang_manager.get_stats()
        
        assert "active_language" in stats
        assert "available_languages" in stats
        assert "translation_cache_size" in stats