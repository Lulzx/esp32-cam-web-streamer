# Codec research — beating MJPEG on the ESP32-CAM

An experiment in real-time video compression for a severely constrained device,
done **entirely in simulation on a laptop** so we never burned a reflash cycle to
learn something. Everything here is reproducible from [`sim/`](../sim).

## The problem, from first principles

The streamer ships **Motion-JPEG**: a full, independent JPEG every frame, even when
you point the camera at a wall. Measured ceilings on the real hardware:

- **Sensor / JPEG encode ≈ 24 FPS** (silicon; can't be beaten in software)
- **WiFi ≈ 1.8 Mbps** (single-stream 2.4 GHz, one core)

So `FPS = min(24, throughput ÷ frame_size)`. Above VGA, **throughput binds** — and
MJPEG wastes it by re-sending unchanged pixels. That waste is the only real lever.

The other defining fact: a **brutal compute asymmetry** — a 240 MHz ESP32 encoder
vs. a multi-GHz browser decoder. The right codec puts ~nothing in the encoder.

## Codecs evaluated

| Codec | Idea | Lineage |
|---|---|---|
| **DeltaCam** | Tile change-detection vs. last frame; send dirty tiles | conditional replenishment |
| **BG-Δ** | Same, but vs. an EMA background model | background subtraction |
| **MoCo-residual** | Global motion-comp + send JPEG of the residual | P-frame / inter coding |
| **ReCAST** *(novel)* | Content-addressed block **dictionary**: hash each block, send only blocks the decoder has never seen | rsync/dedup × video |

### ReCAST in one paragraph

Treat video as a stream of *references* into a block dictionary the decoder owns.
The encoder perceptual-hashes each 16×16 block (a 64-bit *aHash*), delta-codes the
hash grid, and ships pixels only for hashes the decoder reports as new. **Moving
content becomes a cache hit at a new position → motion compensation for free, with no
motion search** (the thing the ESP32 can't afford). Recurring content (a fan, a
pacing person) is reused from arbitrarily far back — something P-frames structurally
can't do.

## Validation 1 — DeltaCam on real cam frames

On a mostly-static SVGA scene **with real sensor noise**, tile-delta cut data **9.6×**:

![DeltaCam vs MJPEG, static SVGA](deltacam_result.png)

But it has failure modes. At UXGA (slow capture → frames far apart) **with auto-exposure
drift**, 74% of tiles went "dirty" and it broke even (≈1×):

![DeltaCam at UXGA — break-even](deltacam_uxga.png)

**Lessons:** (1) lock AEC/AGC/AWB or global luma drift dirties everything; (2) delta
coding helps most when already fast; (3) it degrades *gracefully* (never worse than
MJPEG) — but offers no win on motion.

## Validation 2 — the rate-distortion bake-off

Five codecs on a controlled synthetic stress sequence (static scene + moving box +
AGC drift @ f30–45 + global pan @ f50–65), swept across JPEG quality, scored on
**bytes/frame vs. PSNR**:

![Rate-distortion + per-frame cost](codec_bakeoff.png)

Two findings the test *forced* on us:

1. **ReCAST v1 was brightness-blind.** aHash is luma-invariant (great for noise
   robustness) — so during the AGC ramp it declared "nothing changed" and repainted
   stale-brightness blocks → PSNR stuck at ~21 dB. **Fix (v2): ship a 1-byte luma
   mean per block and brightness-correct cached blocks on reconstruction.** PSNR
   jumped to ~30 dB for +0.4 KB/frame.

2. **The per-frame cost timeline is the real story.** DeltaCam spikes **10–40×**
   (to ~40 KB) the instant AGC or pan hits. ReCAST v2 stays **flat at ~2.5 KB with no
   AGC spike** and only a gentle rise during the pan. For a *fixed* 1.8 Mbps pipe, a
   flat profile beats a low average — spikes cause latency and dropped frames.

### Results (640×480, 75-frame stress sequence)

| Codec | KB/frame (low-rate) | PSNR | Notes |
|---|---|---|---|
| MJPEG | 28.6 | 33 dB | no temporal coding |
| DeltaCam | 8.6 | 33 dB | cheap on static, **spikes on global change** |
| MoCo-residual | 8.9 | 33.5 dB | solid all-rounder |
| **ReCAST v2** | **3.7** | 27–31 dB | **lowest + flattest**, 64% cache-hit |

## Honest conclusions

- **ReCAST reaches an ultra-low, spike-free bitrate band no other codec reaches**
  (~3–7 KB/frame) at usable quality — ideal for a bandwidth-starved link.
- It **caps around ~31 dB** (lossy block reuse); for crisp high-fidelity, MoCo or
  DeltaCam win. Different tools for different regimes.
- **The gating unknown is ESP32 compute** — per-frame block hashing may or may not fit
  the CPU budget. Simulation proves the *compression*; only on-device profiling proves
  *feasibility*. That's the honest next step before any firmware port.
- **100× was never on the table** — the sensor caps at ~24 FPS. The real win is
  ~10× less data → high resolution *and* smoothness on the same pipe.

## Reproduce

```bash
pip install numpy opencv-python matplotlib
python3 sim/deltacam_sim.py            # DeltaCam vs MJPEG on live cam frames
python3 sim/codec_bakeoff.py           # 5-codec rate-distortion bake-off (synthetic)
```

`deltacam_sim.py` pulls real frames from the camera's `/stream`; `codec_bakeoff.py`
runs on a self-contained synthetic stress sequence (no hardware needed).
