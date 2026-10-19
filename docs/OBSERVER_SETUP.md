# Observer Setup Guide — Tournament Day (16 Phones → Director)

Phase 17 (W4). This is the physical rig behind `GET /api/v1/video/status`:
16 Android phones in BGMI spectator mode, each feeding one observer slot
(1–16) on the director wall. Read this **before** tournament day — most
failures in this rig are hardware/USB problems, not software problems.

> **Honest scope note:** the software side (multi-device capture manager,
> auto-reconnect, thumbnails, on-air selection) was developed and tested
> **only against a fake ADB binary in this VM** (see
> `backend/tests/unit/test_vision/test_multi_device_manager.py`). It has
> never seen 16 real phones. Budget a full dry-run day with real hardware
> before any live tournament.

---

## 1. What you actually need (hardware)

| Item | Qty | Notes |
|---|---|---|
| Android phones/tablets | 16 | Android 9+, 4 GB RAM min. They only run BGMI in spectator mode — old flagships are fine. |
| Server PC/laptop | 1 | Runs AEGIS backend + captures 16 feeds. 8+ CPU cores, 16 GB RAM recommended (see §6). No GPU needed for capture. |
| **Powered** USB 3.0 hubs | 3–4 | This is the part people cheap out on. See §2. |
| USB A→C / micro cables | 16+ spares | Short, data-capable cables (charge-only cables silently fail ADB). |
| 5 GHz Wi-Fi router (optional) | 1 | Only if you go the Wi-Fi ADB route (§3). A cheap dedicated router beats venue Wi-Fi. |
| Power strips / chargers | — | 16 phones at full brightness + BGMI drain fast. Plug every hub and as many phones as possible into mains. |
| Labels / tape | — | Number the phones 1–16 with tape. Slot mapping is by ADB serial, but humans need the tape. |

**Phone assignment:** one phone per team being spectated (16 teams). The
mapping slot→team lives in the database (`PUT /api/v1/observers/{n}/assign`),
NOT in the capture software — renaming a team in the DB updates every
label automatically.

---

## 2. USB hubs — the part that actually matters

`adb_poll` capture = `adb exec-out screencap -p` in a loop per phone. Each
screenshot is a PNG of ~0.5–2 MB; at 5–10 fps that is **~2.5–20 MB/s per
phone**. Plan bandwidth like it is the bottleneck, because it is:

- **Use powered USB 3.0 (or 3.2) hubs.** Unpowered hubs brown out with 4+
  phones charging; USB 2.0 hubs top out at ~35 MB/s real throughput ≈
  2–4 phones at full rate.
- **Rule of thumb: max 4 phones per USB 2.0 hub, max 8 per good USB 3.0 hub.**
  For 16 phones: 4× USB 3.0 powered hubs (4 phones each) is the safe,
  boring, correct answer. Spread them across the server's USB root ports —
  two hubs on the same root port share its bandwidth.
- **Plug hubs into mains power.** A phone running BGMI at full brightness
  draws more than a bus-powered hub can supply; voltage sag causes the
  exact "device flapping online/offline" failure in §7.
- **Cable quality is not a myth.** Charge-only cables (no data pins) are
  the #1 cause of "phone charges but `adb devices` shows nothing". Test
  every cable with `adb devices` the night before and throw the bad ones
  away — do not "keep them as spares".
- If you can install it, **prefer the `scrcpy` backend** over `adb_poll`
  for 16 devices: H.264 at ~1–2 Mbps per phone vs ~20–160 Mbps of PNGs.
  Set `capture_backend="scrcpy"` on the manager (or ask the operator who
  configured the server). See §4 for scrcpy flags.

---

## 3. Wi-Fi ADB setup (alternative to USB)

Useful when phones must be spread around a venue, or you run out of USB
ports. **Every phone needs a stable IP** — flapping Wi-Fi = flapping feeds.

On each phone (one time, with USB connected):

```bash
adb -s <serial> tcpip 5555        # restart adbd in TCP mode on port 5555
adb connect 192.168.1.101:5555    # connect over Wi-Fi
adb devices                       # should show 192.168.1.101:5555  device
```

Then you can unplug USB. Checklist:

1. Give every phone a **DHCP reservation** (static IP) on the router, or
   the slot mapping breaks every time a lease renews.
2. Use a **dedicated 5 GHz router** for the phones — not venue Wi-Fi, not
   a phone hotspot. 16 phones × screencap PNGs will saturate 2.4 GHz.
3. `adb tcpip` resets on reboot — after any phone restart, re-run the
   `tcpip` + `connect` steps (USB needed once for the `tcpip` step).
4. Expect +50–200 ms latency vs USB and occasional stalls; the manager
   reports these as `reconnecting`/`no_feed` — that is honest, not a bug.
5. On tournament day, prefer USB for the phones nearest the server and
   Wi-Fi only for the overflow.

---

## 4. scrcpy options (when using the scrcpy backend)

If `scrcpy` is installed on the server, the manager can use persistent
H.264 streams instead of PNG polling — much kinder to USB bandwidth and
CPU. Recommended flags (set on the `ScrcpyCapture` / manager config):

```bash
scrcpy --max-size 960 --max-fps 15 --no-audio \
       --video-bit-rate 2M --no-window
```

- `--max-size 960`: 960px on the long edge is plenty for thumbnails and
  kill-feed OCR; halves the decode cost.
- `--max-fps 15`: spectator feeds do not need 60 fps; 15 fps is smooth
  enough for the director wall and keeps 16 decoders affordable on CPU.
- `--no-audio`: no audio forwarding — saves bandwidth and avoids 16
  phantom audio devices.
- `--video-bit-rate 2M`: ~2 Mbps per phone ≈ 32 Mbps total — fits one
  USB 3.0 hub comfortably.
- `--no-window`: headless on the server (the director watches the web UI,
  not 16 scrcpy windows).

Per-phone CPU decode cost on the server is roughly one thread at 960p15 —
budget accordingly (§6).

---

## 5. Tournament-day checklist (16 devices)

**Night before:**
- [ ] All 16 phones charged to 100%, plugged into mains overnight.
- [ ] Every cable tested with `adb devices` (toss charge-only cables).
- [ ] Developer options → USB debugging ON on every phone; **authorize the
      server's RSA key** on every phone (tap "Always allow"). This is the
      step everyone forgets — an unauthorized phone shows as
      `unauthorized` in `adb devices` and the manager cannot use it.
- [ ] Screen lock / PIN disabled; "Stay awake while charging" ON;
      screen timeout → 30 minutes or Never.
- [ ] BGMI installed, logged in, spectator/observer mode verified working
      on each phone. Join the tournament room once as a rehearsal.
- [ ] Phones labeled 1–16 with tape (matches your slot plan on paper).
- [ ] Server: `adb devices` shows all 16 serials as `device`.

**Morning of:**
- [ ] `POST /api/v1/video/discover` → confirm `slots_mapped: 16`.
- [ ] `POST /api/v1/video/capture/start` → `GET /api/v1/video/status` →
      all 16 slots `connected`, thumbnails loading on the director wall.
- [ ] Assign slots to teams: `PUT /api/v1/observers/{n}/assign`
      (or do this in the admin UI) — labels update live, no restart needed.
- [ ] Spot-check 3–4 thumbnails: correct team on the correct slot.
- [ ] Kill one phone's BGMI (rehearse a failure): slot should flip to
      `no_feed`/`reconnecting`, not crash the wall. Reopen BGMI: it should
      recover on its own (auto-reconnect with backoff).
- [ ] Keep 2 spare phones + cables at the desk. You will need them.

---

## 6. Server sizing (honest numbers)

- `adb_poll` backend: ~16 subprocess spawns/sec total, PNG decode ~2.3 fps
  per feed on CPU-only (Phase 14 measurement) — the **director wall is
  fine** (thumbnails refresh every ~2 s), but do NOT expect 16 smooth
  30 fps video streams from this backend.
- `scrcpy` backend: ~16 H.264 decoders at 960p15 — comfortable on a
  modern 8-core CPU, heavy on a 4-core laptop.
- RAM: ~100–200 MB per feed buffer worst case → 16 GB is comfortable.
- Disk: capture itself stores nothing (frames are in-memory ring
  buffers); only enable recording if you have TBs free.

---

## 7. Troubleshooting

| Symptom | Cause → Fix |
|---|---|
| `adb devices` shows `unauthorized` | Phone hasn't accepted the server's RSA key → on the phone, revoke USB debugging authorizations, replug, tap "Always allow". |
| Device shows `offline` | adbd crashed or cable flapping → unplug/replug; try a different cable/port; `adb reconnect`. |
| `no permissions` (Linux) | udev rules missing → add the standard android udev rules, or run the adb server once with sudo (then `adb kill-server` as your user). |
| Two devices, same serial `emulator-5554` | Two phones both in Wi-Fi ADB on the same forwarded port, or stale `adb connect` entries → `adb disconnect`, re-`connect` each phone to its own IP. |
| Slot flaps connected↔disconnected | Power sag on an unpowered hub, or a dying cable → powered hub, new cable, different root port. |
| Slot stuck `no_feed`, phone fine | BGMI killed by the OS (battery optimization) → disable battery optimization for BGMI, lock it in recents. |
| Slot stuck `reconnecting` for minutes | Phone actually gone (dead battery, Wi-Fi drop) → check the phone physically; the manager retries forever with capped backoff, so it recovers on its own when the phone returns. |
| Thumbnails black but status `connected` | Screen off / screen lock engaged → disable lock, "stay awake while charging". |
| `adb server version (xx) doesn't match` | Two adb binaries fighting → `adb kill-server`, use one adb everywhere. |
| Everything was fine, then all 16 drop | Hub power supply died or venue power blip → check hub LEDs first, not the software. |

---

## 8. Software quick reference (AEGIS side)

```bash
# After phones are connected and authorized:
curl -X POST localhost:8000/api/v1/video/discover      # map serials -> slots
curl -X POST localhost:8000/api/v1/video/capture/start  # start 16 feeds
curl        localhost:8000/api/v1/video/status          # director wall data
# Director picks the program feed:
curl -X POST localhost:8000/api/v1/video/on-air \
     -H 'Content-Type: application/json' -d '{"slot_number": 3}'
# Thumbnail for slot 5 (JPEG; 404 = honest no-feed):
curl localhost:8000/api/v1/video/thumbnail/5 -o s5.jpg
```

Auth: status/thumbnail need any logged-in user; on-air/discover/start/stop
need operator or admin. WS events `video.feeds` and `video.on_air` arrive
on `/ws/live` for live UI updates.
