"""Phase 17 (W4) — video API package (observer feeds / director wall).

Coordination note: this router was created by W4 (observer ingest) because
no video router existed yet. W2 (video layer) owns everything that COMPOSES
or STREAMS these feeds; this router only reports feed state, serves
thumbnails, and records the director's on-air selection. The full contract
is documented at the bottom of
``app/vision/capture/multi_device.py`` -- read it before extending this.
"""
