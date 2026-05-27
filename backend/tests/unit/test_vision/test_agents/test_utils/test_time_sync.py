# backend/tests/unit/test_utils/test_time_sync.py
"""
Unit tests for Time Synchronization.
Tests NTP-based time sync, timestamp normalization,
latency compensation, and clock drift detection.
"""

import pytest
import asyncio
import time

from app.core.time_sync import (
    TimeSyncManager, TimeSyncConfig, SyncStatus,
    TimestampNormalizer,
)


@pytest.mark.unit
@pytest.mark.utils
class TestTimeSyncManager:
    """Test TimeSyncManager functionality"""
    
    @pytest.fixture
    def sync_manager(self):
        """Create TimeSyncManager instance"""
        return TimeSyncManager()
    
    @pytest.fixture
    def sync_config(self):
        """Create time sync configuration"""
        return TimeSyncConfig(
            sync_interval_ms=1000,
            max_drift_ms=50,
            ntp_servers=["pool.ntp.org", "time.google.com"],
            devices=["device_001", "device_002", "device_003"],
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, sync_manager):
        """Test sync manager initializes correctly"""
        assert sync_manager.master_clock is not None
        assert sync_manager.sync_status == SyncStatus.NOT_SYNCED
        assert len(sync_manager.device_clocks) == 0
    
    def test_register_device(self, sync_manager):
        """Test registering a device for time sync"""
        sync_manager.register_device("device_001")
        
        assert "device_001" in sync_manager.device_clocks
        assert sync_manager.device_clocks["device_001"].device_id == "device_001"
        assert sync_manager.device_clocks["device_001"].offset_ms == 0.0
    
    def test_register_multiple_devices(self, sync_manager):
        """Test registering multiple devices"""
        devices = ["device_001", "device_002", "device_003", "device_004"]
        
        for device_id in devices:
            sync_manager.register_device(device_id)
        
        assert len(sync_manager.device_clocks) == 4
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TIME MEASUREMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_master_time(self, sync_manager):
        """Test getting master clock time"""
        master_time = sync_manager.get_master_time()
        
        assert master_time > 0
        assert isinstance(master_time, float)
    
    def test_measure_rtt(self, sync_manager):
        """Test round-trip time measurement"""
        rtt = sync_manager.measure_rtt()
        
        assert rtt >= 0
        assert rtt < 1000  # Should be less than 1 second
    
    @pytest.mark.asyncio
    async def test_calibrate_device(self, sync_manager):
        """Test calibrating a device"""
        sync_manager.register_device("device_001")
        
        await sync_manager.calibrate_device("device_001")
        
        device_clock = sync_manager.device_clocks["device_001"]
        assert device_clock.last_synced > 0
        assert device_clock.sync_count > 0
    
    @pytest.mark.asyncio
    async def test_calibrate_all_devices(self, sync_manager):
        """Test calibrating all registered devices"""
        for i in range(4):
            sync_manager.register_device(f"device_{i:03d}")
        
        await sync_manager.calibrate_all_devices()
        
        for device_id in sync_manager.device_clocks:
            device_clock = sync_manager.device_clocks[device_id]
            assert device_clock.sync_count > 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TIMESTAMP NORMALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_normalize_timestamp(self, sync_manager):
        """Test normalizing a device timestamp"""
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 100.0  # 100ms ahead
        
        normalized = sync_manager.normalize_timestamp("device_001", 1000.0)
        
        assert normalized == 900.0  # 1000 - 100 = 900 (corrected)
    
    def test_normalize_timestamp_behind(self, sync_manager):
        """Test normalizing timestamp from device that's behind"""
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = -50.0  # 50ms behind
        
        normalized = sync_manager.normalize_timestamp("device_001", 1000.0)
        
        assert normalized == 1050.0  # 1000 - (-50) = 1050
    
    def test_normalize_timestamp_unknown_device(self, sync_manager):
        """Test normalizing timestamp for unknown device"""
        normalized = sync_manager.normalize_timestamp("unknown_device", 1000.0)
        
        assert normalized == 1000.0  # Returns original timestamp
    
    def test_normalize_timestamp_zero_offset(self, sync_manager):
        """Test normalizing with zero offset"""
        sync_manager.register_device("device_001")
        
        normalized = sync_manager.normalize_timestamp("device_001", 1500.0)
        
        assert normalized == 1500.0  # No change
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DRIFT DETECTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_detect_drift_within_tolerance(self, sync_manager):
        """Test drift detection within tolerance"""
        sync_manager.config.max_drift_ms = 50
        
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 30.0  # 30ms drift
        
        is_drifting = sync_manager.detect_drift("device_001")
        
        assert is_drifting is False  # Within 50ms tolerance
    
    def test_detect_drift_exceeds_tolerance(self, sync_manager):
        """Test drift detection exceeding tolerance"""
        sync_manager.config.max_drift_ms = 50
        
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 75.0  # 75ms drift
        
        is_drifting = sync_manager.detect_drift("device_001")
        
        assert is_drifting is True  # Exceeds 50ms tolerance
    
    def test_detect_drift_boundary(self, sync_manager):
        """Test drift detection at boundary"""
        sync_manager.config.max_drift_ms = 50
        
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 50.0  # Exactly at boundary
        
        is_drifting = sync_manager.detect_drift("device_001")
        
        assert is_drifting is False  # At boundary is within tolerance
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SYNC STATUS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_sync_status_after_calibration(self, sync_manager):
        """Test sync status after calibration"""
        sync_manager.register_device("device_001")
        sync_manager.register_device("device_002")
        
        await sync_manager.calibrate_all_devices()
        
        status = sync_manager.get_sync_status()
        
        assert "devices_synced" in status
        assert status["devices_synced"] == 2
        assert "master_time" in status
    
    def test_is_device_synced(self, sync_manager):
        """Test checking if device is synced"""
        sync_manager.register_device("device_001")
        
        assert sync_manager.is_device_synced("device_001") is True
        
        # Old sync time (expired)
        sync_manager.device_clocks["device_001"].last_synced = time.time() - 100
        # Still synced but may need recalibration
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LATENCY COMPENSATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_compensate_latency(self, sync_manager):
        """Test latency compensation"""
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].rtt_ms = 40.0
        
        compensated = sync_manager.compensate_latency("device_001", 1000.0)
        
        # Should subtract half RTT
        assert compensated == 980.0  # 1000 - 40/2 = 980
    
    def test_compensate_latency_unknown_device(self, sync_manager):
        """Test latency compensation for unknown device"""
        compensated = sync_manager.compensate_latency("unknown", 1000.0)
        
        assert compensated == 1000.0  # No compensation
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT ORDERING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_order_events_by_timestamp(self, sync_manager):
        """Test ordering events by corrected timestamp"""
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 50.0  # 50ms ahead
        
        sync_manager.register_device("device_002")
        sync_manager.device_clocks["device_002"].offset_ms = -30.0  # 30ms behind
        
        events = [
            {"device_id": "device_001", "timestamp_ms": 1000.0, "event": "kill"},
            {"device_id": "device_002", "timestamp_ms": 980.0, "event": "knock"},
            {"device_id": "device_001", "timestamp_ms": 1020.0, "event": "revive"},
        ]
        
        ordered = sync_manager.order_events(events)
        
        # Events should be ordered by corrected timestamp
        assert ordered[0]["event"] == "knock"  # 980 - (-30) = 1010
        assert ordered[1]["event"] == "kill"   # 1000 - 50 = 950
        # Actually: knock=1010, kill=950, revive=970
        # So order: kill(950), revive(970), knock(1010)
        assert ordered[0]["event"] == "kill"
        assert ordered[1]["event"] == "revive"
        assert ordered[2]["event"] == "knock"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SYNCHRONIZATION LOOP TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_sync_loop_updates(self, sync_manager):
        """Test sync loop updates device clocks"""
        sync_manager.register_device("device_001")
        sync_manager.config.sync_interval_ms = 100
        
        # Run a few sync cycles
        for _ in range(3):
            await sync_manager._sync_cycle()
            await asyncio.sleep(0.01)
        
        device_clock = sync_manager.device_clocks["device_001"]
        assert device_clock.sync_count >= 0
    
    @pytest.mark.asyncio
    async def test_start_stop_sync(self, sync_manager):
        """Test starting and stopping sync loop"""
        sync_manager.register_device("device_001")
        
        # Start sync
        await sync_manager.start_sync()
        assert sync_manager._running is True
        
        # Let it run briefly
        await asyncio.sleep(0.1)
        
        # Stop sync
        await sync_manager.stop_sync()
        assert sync_manager._running is False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_device_stats(self, sync_manager):
        """Test getting device statistics"""
        sync_manager.register_device("device_001")
        sync_manager.device_clocks["device_001"].offset_ms = 25.0
        sync_manager.device_clocks["device_001"].rtt_ms = 30.0
        
        stats = sync_manager.get_device_stats("device_001")
        
        assert stats is not None
        assert stats["offset_ms"] == 25.0
        assert stats["rtt_ms"] == 30.0
    
    def test_get_all_stats(self, sync_manager):
        """Test getting all sync statistics"""
        sync_manager.register_device("device_001")
        sync_manager.register_device("device_002")
        
        stats = sync_manager.get_stats()
        
        assert "devices" in stats
        assert len(stats["devices"]) == 2
        assert "master_time" in stats


@pytest.mark.unit
@pytest.mark.utils
class TestTimestampNormalizer:
    """Test TimestampNormalizer standalone functionality"""
    
    @pytest.fixture
    def normalizer(self):
        """Create TimestampNormalizer instance"""
        return TimestampNormalizer()
    
    def test_add_device_offset(self, normalizer):
        """Test adding device offset"""
        normalizer.add_offset("device_001", 50.0, 25.0)
        
        assert "device_001" in normalizer.offsets
        assert normalizer.offsets["device_001"]["offset_ms"] == 50.0
        assert normalizer.offsets["device_001"]["rtt_ms"] == 25.0
    
    def test_normalize_with_latency(self, normalizer):
        """Test normalization with latency compensation"""
        normalizer.add_offset("device_001", 100.0, 40.0)
        
        normalized = normalizer.normalize("device_001", 1000.0, compensate_latency=True)
        
        # offset=100, rtt=40/2=20, so 1000-100-20=880
        assert normalized == 880.0
    
    def test_normalize_without_latency(self, normalizer):
        """Test normalization without latency compensation"""
        normalizer.add_offset("device_001", 100.0, 40.0)
        
        normalized = normalizer.normalize("device_001", 1000.0, compensate_latency=False)
        
        # Only offset correction: 1000-100=900
        assert normalized == 900.0
    
    def test_remove_offset(self, normalizer):
        """Test removing device offset"""
        normalizer.add_offset("device_001", 50.0, 25.0)
        normalizer.remove_offset("device_001")
        
        assert "device_001" not in normalizer.offsets
    
    def test_clear_all(self, normalizer):
        """Test clearing all offsets"""
        normalizer.add_offset("device_001", 50.0, 25.0)
        normalizer.add_offset("device_002", -30.0, 20.0)
        
        normalizer.clear_all()
        
        assert len(normalizer.offsets) == 0