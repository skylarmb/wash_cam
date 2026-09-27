# Capture loop lost time to idle sleep

The first unattended collection run captured 17 images over 8 minutes, then nothing for 2h45m, then resumed on its own. Diagnosed from evidence rather than assumption:

- The loop process was still alive throughout, so it had been suspended, not killed.
- `pmset -g log` showed `Entering Sleep state due to 'Idle Sleep'` at 16:55:39, eight seconds after the last capture, and `Wake ... due to HID Activity` at 19:40:03, fifteen seconds before it resumed.
- `pmset -g custom` shows `sleep 1` on AC power, so the machine idles into sleep aggressively.

Worth being precise about the fix: nothing can capture *during* sleep. The CPU is suspended and the USB camera is powered down, and Dark Wake / Power Nap does not run arbitrary user processes or grant camera access. The only option is to prevent sleep for the duration of the run.

`scripts/capture_loop.sh` now re-execs itself under `caffeinate -ims` (no idle sleep, no disk sleep, no system sleep on AC), guarded by a `CAPTURE_LOOP_CAFFEINATED` env var so the re-exec happens once. This is scoped to the run and reverts when it ends, unlike `sudo pmset -a disablesleep 1`, which persists system-wide until someone remembers to undo it. Display sleep is deliberately left enabled — the screen turning off does not affect capture, and keeping it on wastes power.

Verified after restart: `pmset -g assertions` shows the loop's caffeinate holding `PreventUserIdleSystemSleep`, `PreventSystemSleep`, and `PreventDiskIdle`.

Remaining limitation: closing a MacBook lid still sleeps the machine regardless of caffeinate, unless it is in clamshell mode on AC with an external display attached.
