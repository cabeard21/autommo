# Cooldown Reader

A Python desktop app that reads MMO action bar cooldowns via screen capture and image analysis, then optionally sends keypresses in priority order when abilities are ready.

## What It Does

- **Captures** a configurable screen region (your action bar) at high frame rates
- **Detects** per-slot state (**ready**, **casting/channeling**, **on cooldown**, optional **locked**) by comparing live frames to calibrated baselines with temporal state tracking
- **Shows** a transparent overlay so you can align the capture region with your action bar
- **Displays** live slot states (green = ready, blue = casting, amber = channeling, red = on cooldown, gray = locked)
- **Automation**: when enabled, presses keys in a **priority order**—only the first *ready* ability in the list is triggered, with a minimum delay between keypresses, cast-aware blocking/queue timing, and an optional “target window” check so keys are only sent when the game has focus
- **Per-slot activation rules**: each slot item in Priority can be set to **Always** or **DoT refresh** (`no glow` or `red glow`)

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
```

## Run (as Administrator)

```bash
python -m src.main
```

---

## How to Operate

### First-Time Setup

1. **Monitor** — Choose the display that shows your game.
2. **Show Region Overlay** — Keep this checked to see a green rectangle on screen. Move it over your action bar.
3. **Capture Region** — Set **Top**, **Left**, **Width**, and **Height** (pixels) so the overlay exactly covers the action bar. Use **Slots**, **Gap**, and **Padding** to match how your bar is divided (e.g. 12 slots, 2 px gap).
4. **Calibrate Baselines** — With all abilities *off* cooldown, click **Calibrate Baselines**. The app stores the “ready” appearance of each slot. Do this once per bar layout; you can recalibrate later if needed.
5. **Start Capture** — Click **Start Capture**. The **Live Preview** and **Slot States** will update in real time. Each slot shows its keybind and state (ready, casting/channeling, cooldown, or locked).

### Slot States (Left Panel)

Each slot is a button showing `[key]` and state:
- **Green**: ready
- **Blue**: casting
- **Amber**: channeling
- **Red**: on cooldown
- **Gray**: locked (optional global lock from cast-bar ROI)

- **Right-click** a slot to open the context menu:
  - **Bind Key** — Set which key this slot sends (e.g. `1`, `2`, `f`, `f5`, `ctrl+1`, `shift+1`). Click the option, then press the key/combo you want. The slot label updates to that bind.
  - **Calibrate This Slot** — Recalibrate only this slot’s baseline from the current frame (useful if one ability is 'disabled' while you calibrate baseline).
  - **Rename...** — Give the slot a display name (e.g. “Fireball”) so it’s easier to recognize in the priority list.
- **Drag** a slot (left-click and drag) **into the Priority list** on the right to add it to the automation order. You can only add slots that have a keybind set.

### Priority List (Right Panel)

The order here is the **priority order** for the currently active automation profile: the app will press the key of the **first** slot in the list that is **ready**.

- **Add slots** — Drag slot buttons from the left (Slot States) and drop them onto the Priority list.
- **Reorder** — Drag a priority item up or down within the list to change order.
- **Remove** — Drag a priority item outside of the **Priority panel** It will be removed from the list.
- **Slot activation rule** — Right-click a slot item in the Priority list:
  - **Activation: Always** — sends whenever that slot is ready.
  - **Activation: DoT refresh (no glow or red)** — sends only when slot is ready and either no glow is found or red glow is active (yellow-only glow is blocked).

The right panel also shows:

- **Last Action** — The last key that was actually sent and how many seconds ago (updates every 100 ms while automation is on; stops when you disable automation).
- **Next Intention** — The next key that *would* be pressed (first ready slot in priority), with a suffix:
  - `(paused)` — Automation is off.
  - `(window)` — Automation is on but the target window (e.g. game) is not focused; keys are not sent.
  - `waiting: casting` / `waiting: channeling` — Automation is paused by cast-time safety rules.
  - `next` — Automation is on and the game is focused; this key will be sent when the minimum delay has passed.

### Automation (Left Panel)

- **Enable / Disable** — Green **Enable** turns automation on (sends keys in priority order); red **Disable** turns it off. Automation always starts **off** when you launch the app.
- **List profiles** — In **Settings → Automation**, use **+ / Copy / −** to create, duplicate, or remove list profiles (for example: Single Target, AoE, Utility). Each profile stores its own priority list.
- **Per-profile hotkeys** — Each list profile has two optional global binds (single key or combo like `ctrl+f24`):
  - **Toggle bind** — Switches to that profile and toggles continuous automation on/off.
  - **Single bind** — Switches to that profile and arms exactly one next action (single fire) without enabling continuous automation.
- **Conflict warning** — If a bind is reused across profiles (or toggle/single collide), Settings shows a red conflict badge so you can resolve it quickly.
- **Delay (ms)** — Minimum time in milliseconds between any two keypresses (50–2000). Helps avoid spamming faster than the game’s GCD.
- **GCD (ms)** — Duration used to suppress normal priority after a queued key is sent (default 1500 ms).
- **Queue (ms)** — Extra wait after detected cast end before sending the next key (default 120 ms).
- **Allow sends while casting/channeling** — If off (default), automation waits until cast/channel completes.
- **Window title** — If you type part of the game window title here (e.g. `World of Warcraft`), keypresses are **only** sent when a window whose title contains this text (case-insensitive) is in the foreground. Leave blank to send keys regardless of focus.
- **Queue timeout / Fire delay** — Queue timeout controls how long a queued input is kept; fire delay adds a small post-ready delay before queued send.

### Other Settings

- **Always on top** — Check to keep the app window above other windows.
- **Detection** — **Polling FPS**, **Cooldown min**, **Darken**, **Trigger**, **Yellow frac**, and **Red frac** control responsiveness and cooldown/glow sensitivity.
- **Red glow threshold** — Tune **Red frac** if DoT-refresh slots are triggering too early or too late on red-glow windows.
- **Per-slot glow sensitivity (advanced)** — In `config/default_config.json`, set `detection.glow_value_delta_by_slot` (example: `{"4": 55}`) to lower/raise glow brightness delta for one slot without changing others.
- **Per-slot glow thresholds (advanced)** — Use `detection.glow_ring_fraction_by_slot` (example: `{"5": 0.08}`) and `detection.glow_override_cooldown_by_slot` (example: `[5]`) for proc-style icons that need lower yellow-fraction threshold and optional non-red glow cooldown override.
- **Cast detection** — Configure cast band %, confirmation frames, min/max cast duration, cancel grace, and channeling mode.
- **Cast bar ROI (optional)** — Define a region inside the capture box to detect active cast-bar motion; optionally mark ready slots as `locked` while active.
- **Save Settings** — Saves the current config (region, slots, keybinds, priority profiles + binds, detection, overlay, delay, window title, etc.) to `config/default_config.json`.

---

## Rebind Summary

| What to rebind | How |
|----------------|-----|
| **Slot key** (which key the app presses for that slot) | Right-click the slot → **Bind Key** → press the key/combo |
| **Automation list hotkeys** (global actions per profile) | In **Settings → Automation**: pick the list profile, then set **Toggle bind** and/or **Single bind** |

---

## Project Structure

```
autowow/
├── README.md
├── requirements.txt
├── config/
│   └── default_config.json
└── src/
    ├── main.py              # Entry point, wiring
    ├── capture/             # Screen capture (mss)
    ├── analysis/            # Slot detection
    ├── overlay/             # Calibration overlay
    ├── ui/                  # PyQt6 control panel
    ├── automation/          # Key sending, global hotkey
    └── models/              # Config and state types
```

## Requirements

- Python 3.11+
- Game in **borderless windowed** (or windowed) mode so the overlay can be drawn and the target window title can be read
- Windows recommended for the “target window” check (uses Win32 API); key sending works with the `keyboard` library on supported platforms

## Manual Actions (Not Tied to Slots)

You can now add priority entries that are not linked to a monitored icon slot.

- In the **Priority** panel, click **+ manual**.
- Enter an action name and keybind.
- The manual action is added to the current profile's priority list.
- Right-click a manual item in the priority list to **Rename**, **Rebind**, or **Remove**.

Behavior in v1:
- Manual actions are treated as eligible whenever they have a keybind.
- They still respect automation safety gates (minimum delay, casting/channel blocking, target-window check).
- If a manual action is placed above monitored slots, it may be selected repeatedly.
