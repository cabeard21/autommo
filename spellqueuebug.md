# Spell queue bug (resolved)

Post-mortem for the bug where a **queued** key (e.g. V) was supposed to fire at the next GCD but the **priority** key (e.g. 3) fired instead. The app logic was correct; the fix was to delay sending the queued key until the game’s GCD was actually ready.

---

## The bug

- User queues a key (e.g. V) via the spell queue. Next Intention correctly shows “V queued.”
- When GCD becomes ready, the app is supposed to send the **queued** key (V). Instead, in-game the **priority** key (e.g. 3) appeared to fire; the queued key did not.
- It looked like the queued skill was being “replaced” by the priority skill in both the UI and in what the game received.

---

## What we tried (and why it wasn’t the fix)

1. **80 ms delay before sending the queued key** (to avoid sending while the physical key was still down) — reverted; no improvement.

2. **Only send the queued key when `any_priority_ready`** (use priority slot READY as the “GCD over” signal) — logic was correct and kept; didn’t fix the issue.

3. **`keyboard.press(key)` + 50 ms + `keyboard.release(key)`** instead of `keyboard.send(key)` for queued keys — tried so the game would see a full key stroke; game still didn’t reliably get the queued key.

4. **Suppress priority for 1.5 s after sending the queued key** — so only the queued key is sent in that window; correct behavior, kept, but didn’t fix the underlying problem.

5. **Snapshot queue at start of worker tick** (get queue before `state_updated.emit`) — so the same tick doesn’t “replace” the queue; good consistency, kept.

6. **Guard: never run the priority path when `queued_override` is set** — if we had a queue but didn’t send (e.g. wrong source), return without sending priority; defensive, kept.

7. **Use `keyboard.send(key)` for queued keys** (same API as priority keys) — in case the game only accepted that kind of event; still didn’t fix it.

8. **Debug logging** — showed we *were* sending the queued key when we had a queue and never sending the priority key in that case. So the bug was not “we send the wrong key”; it was timing.

---

## Root cause

We send the queued key as soon as we see **any priority slot READY** (our proxy for “GCD is over”). The **client’s visual** cooldown can turn READY a frame or a few dozen ms **before** the game server actually allows the next input. So we were sending the queued key **too early**; the game was still on GCD and ignored it. The next key that was accepted was the priority key (after the 1.5 s suppression), so it looked like the priority key “replaced” the queued one.

---

## Actual fix

**Queue fire delay:** wait a short time **after** we detect “GCD ready” (any priority slot READY) before sending the queued key, so we’re past the moment when the client is ready but the server isn’t.

- **Config:** `queue_fire_delay_ms` (default 100). In Settings → Spell Queue → “Fire delay” (0–300 ms).
- **Behavior:** When we have a queued key and conditions are met (any_priority_ready, min_interval_ok, window_ok), we **sleep for `queue_fire_delay_ms`** then send the queued key with `keyboard.send(key)`.
- **Tuning:** 100 ms default; 0 disables the delay; if the game still eats the input sometimes, try 150 or 200 ms.

**Files changed for this fix:**

- `src/models/slot.py` — `queue_fire_delay_ms` on `AppConfig`, `from_dict` / `to_dict`
- `src/automation/key_sender.py` — sleep for `queue_fire_delay_ms` before sending queued key (whitelist and tracked)
- `src/ui/settings_dialog.py` — “Fire delay” spinbox, sync, and handler

---

## Summary

**Bug:** Queued key appeared not to fire; priority key seemed to replace it.  
**Cause:** We sent the queued key as soon as the UI showed GCD ready; the game wasn’t accepting input yet.  
**Fix:** Add a short configurable delay (`queue_fire_delay_ms`, default 100 ms) after detecting GCD ready before sending the queued key.
