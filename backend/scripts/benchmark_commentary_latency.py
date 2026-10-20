"""Benchmark the REAL commentary pipeline latency (Phase 22).

Stages measured per line, with time.perf_counter():
  1. profanity filter
  2. Piper model load (cold, first line only) vs warm synthesis
  3. end-to-end enqueue_line (filter -> synth -> WAV -> PCM convert/queue)
  4. cached repeat of the same line (LRU hit / pre-synthesis effect)

Run:  cd backend && python scripts/benchmark_commentary_latency.py [--lines N]
Prints honest numbers; on a contended VM they will be seconds, not the
PRD's <600 ms — that is the point of measuring.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=5)
    args = parser.parse_args()

    import os

    os.environ.setdefault("AEGIS_TTS_ENGINE", "piper")

    from app.agents.base_agent import AgentConfig
    from app.agents.commentary_agents.voice_synthesizer import VoiceSynthesizer
    from app.audio.profanity import filter_profanity
    from app.core.event_bus import EventBus

    lines_en = [
        "Jonathan picks up the M416 and holds the bridge beautifully",
        "What a brilliant rotation into the final circle",
        "Team Soul is down to their last player standing",
        "The zone is closing and every second counts now",
        "An incredible spray transfer takes down two players",
    ]
    lines_hi = [
        "SCOUT KYA KHEL RAHA HAI, KAMAAL KAR DIYA",
        "Zone band ho raha hai, teams ko jaldi rotate karna hoga",
    ]

    print("== profanity filter ==")
    ts = []
    for _ in range(50):
        t0 = time.perf_counter()
        filter_profanity("What a brilliant play by the squad")
        ts.append((time.perf_counter() - t0) * 1000)
    print(f"filter: median {statistics.median(ts):.3f} ms over 50 runs")

    synth = VoiceSynthesizer(AgentConfig(agent_id="latency-bench"), EventBus())

    print("== cold model load + first synthesis ==")
    t0 = time.perf_counter()
    audio = await synth.synthesize(lines_en[0], profile_id="english_analyst")
    cold_ms = (time.perf_counter() - t0) * 1000
    print(f"cold (load+synth): {cold_ms:.0f} ms, synthesized={bool(audio and audio.synthesized)}")

    print("== warm synthesis (en + hi) ==")
    warm = []
    for text, profile in [(t, "english_analyst") for t in lines_en[1 : args.lines]] + [
        (t, "hindi_caster_1") for t in lines_hi
    ]:
        t0 = time.perf_counter()
        a = await synth.synthesize(text, profile_id=profile)
        ms = (time.perf_counter() - t0) * 1000
        warm.append(ms)
        print(f"  {ms:8.0f} ms  synth_ok={bool(a and a.synthesized)}  {text[:48]}")
    if warm:
        print(f"warm: median {statistics.median(warm):.0f} ms, min {min(warm):.0f} ms, max {max(warm):.0f} ms")

    print("== cached repeat (pre-synthesis / LRU effect) ==")
    t0 = time.perf_counter()
    a = await synth.synthesize(lines_en[0], profile_id="english_analyst")
    print(f"cache hit: {(time.perf_counter() - t0) * 1000:.1f} ms, source={getattr(a, 'source', None)}")

    print("== end-to-end enqueue_line (feed) ==")
    import tempfile

    from app.audio.commentary_feed import CommentaryAudioFeed

    feed = CommentaryAudioFeed(audio_dir=Path(tempfile.mkdtemp()))
    totals = []
    for text in lines_en[:3]:
        t0 = time.perf_counter()
        res = await feed.enqueue_line(text, priority=100)
        ms = (time.perf_counter() - t0) * 1000
        totals.append(ms)
        print(f"  {ms:8.0f} ms  queued={res.get('queued')} source={res.get('source')}")
    if totals:
        print(f"enqueue: median {statistics.median(totals):.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
