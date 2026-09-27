# Multi-reference calibration, verified on hardware

Two findings drove this change, both measured rather than assumed.

**Day/night references cannot substitute for each other.** Matching a live night frame against the daytime reference gave 4 inliers and solved a +809/+393 px shift at 0.70x scale, despite the camera not having moved. The same frame against itself gave 5000 inliers and exact identity. Calibration now keeps one reference per lighting condition under `calibration/references/`, tries all of them, and keeps the highest inlier count — no brightness classifier, so twilight resolves itself and extra conditions (rain, fog) can be added later by dropping in another reference.

**`estimate_alignment` had a real bug.** It gated on match count (`MIN_MATCHES`) but never on inlier count, so those 4 inliers passed straight through and would have been persisted, silently mis-framing every later capture. Added `MIN_CALIBRATION_INLIERS` and an explicit rejection path. This was found only because the day/night test was run against live hardware; no unit test would have caught it.

Ordering constraint worth knowing: a reference is only useful if it is in canonical coordinates. The night reference was captured *before* the camera was moved, while it still sat at the canonical position. `set_reference()` now warps new references through the existing calibration so this holds automatically, but the first reference defines canonical and cannot be re-derived.

**Hardware test (camera physically unplugged, moved, exposure changed, replugged):**

- Calibration selected `night` over `day` correctly, 164 inliers.
- Measured the real displacement: +62.4/+170.3 px, scale 0.987, rotation 0.48 deg.
- Residual between a pre-move and post-move capture after correction: 3.09 px in x, 1.22 px in y, scale 1.004, rotation -0.02 deg. A ~170 px error corrected to ~3 px.
- No dead pixels inside the crop, so the warp did not pull in uncovered sensor area.
- Inlier margin narrowed sharply, 1820 before the move to 164 after. Still clear of the threshold of 50, but worth watching: a larger move will start hitting the rejection path rather than mis-framing silently.

**Warm-up frames are worth their cost after all.** A steady-state test showed zero benefit (0/4/10 warm-up frames gave identical brightness), but the cold-start comparison is decisive. After a sleep-wake without warm-up, captures ramped 2.0 -> 7.3 -> 13.0 -> 18.5 -> 36.0 -> 47.0 across six 30s cycles, the first being effectively black. After an unplug-replug with warm-up, the first capture was mean 20.7 and the second 38.4 — converged in two captures instead of six, with no unusable frame. The benefit is real but only on a cold sensor.

Validation: 95 tests pass (including another agent's dashboard and labels work), Ruff lint and format, `git diff --check`.
