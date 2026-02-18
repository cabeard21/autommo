# Cooldown Reader

A Python desktop app that reads MMO action bar cooldowns via screen capture and image analysis, then optionally sends keypresses in priority order when abilities are ready.

## What It Does

- **Captures** a configurable screen region (your action bar) at high frame rates
- **Detects** per-slot state (**ready**, **casting/channeling**, **on cooldown**, optional **locked**) by comparing live frames to calibrated baselines with temporal state tracking
- **Shows** a transparent overlay so you can align the capture region with your action bar
- **Displays** live slot states (green = ready, blue = casting, amber = channeling, red = on cooldown, gray = locked)
- **Automation**: when enabled, presses keys in a **priority order**Ã¢â‚¬â€only the first *ready* ability in the list is triggered, with a minimum delay between keypresses, cast-aware blocking/queue timing, and an optional Ã¢â‚¬Å“target windowÃ¢â‚¬Â check so keys are only sent when the game has focus
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

1. **Monitor** Ã¢â‚¬â€ Choose the display that shows your game.
2. **Show Region Overlay** Ã¢â‚¬â€ Keep this checked to see a green rectangle on screen. Move it over your action bar.
3. **Capture Region** Ã¢â‚¬â€ Set **Top**, **Left**, **Width**, and **Height** (pixels) so the overlay exactly covers the action bar. Use **Slots**, **Gap**, and **Padding** to match how your bar is divided (e.g. 12 slots, 2 px gap).
4. **Calibrate Baselines** Ã¢â‚¬â€ With all abilities *off* cooldown, click **Calibrate Baselines**. The app stores the Ã¢â‚¬Å“readyÃ¢â‚¬Â appearance of each slot. Do this once per bar layout; you can recalibrate later if needed.
5. **Start Capture** Ã¢â‚¬â€ Click **Start Capture**. The **Live Preview** and **Slot States** will update in real time. Each slot shows its keybind and state (ready, casting/channeling, cooldown, or locked).

### Slot States (Left Panel)

Each slot is a button showing `[key]` and state:
- **Green**: ready
- **Blue**: casting
- **Amber**: channeling
- **Red**: on cooldown
- **Gray**: locked (optional global lock from cast-bar ROI)

- **Right-click** a slot to open the context menu:
  - **Bind Key** Ã¢â‚¬â€ Set which key this slot sends (e.g. `1`, `2`, `f`, `f5`, `ctrl+1`, `shift+1`). Click the option, then press the key/combo you want. The slot label updates to that bind.
  - **Calibrate This Slot** Ã¢â‚¬â€ Recalibrate only this slotÃ¢â‚¬â„¢s baseline from the current frame (useful if one ability is 'disabled' while you calibrate baseline).
  - **Rename...** Ã¢â‚¬â€ Give the slot a display name (e.g. Ã¢â‚¬Å“FireballÃ¢â‚¬Â) so itÃ¢â‚¬â„¢s easier to recognize in the priority list.
- **Drag** a slot (left-click and drag) **into the Priority list** on the right to add it to the automation order. You can only add slots that have a keybind set.

### Priority List (Right Panel)

The order here is the **priority order** for the currently active automation profile: the app will press the key of the **first** slot in the list that is **ready**.

- **Add slots** Ã¢â‚¬â€ Drag slot buttons from the left (Slot States) and drop them onto the Priority list.
- **Reorder** Ã¢â‚¬â€ Drag a priority item up or down within the list to change order.
- **Remove** Ã¢â‚¬â€ Drag a priority item outside of the **Priority panel** It will be removed from the list.
- **Slot activation rule** Ã¢â‚¬â€ Right-click a slot item in the Priority list:
  - **Activation: Always** Ã¢â‚¬â€ sends whenever that slot is ready.

  - **Ready Source: Use slot icon state** â€” default behavior.
  - **Ready Source: Buff present / Buff missing** â€” uses selected Buff ROI state as an additional gate on top of slot icon readiness.
- **Manual action ready source** Ã¢â‚¬â€ Right-click a manual item in the Priority list:
  - **Ready Source: Always** â€” default behavior for manual actions.
  - **Ready Source: Buff present / Buff missing** â€” sends only when the selected Buff ROI gate passes.

The right panel also shows:

- **Last Action** Ã¢â‚¬â€ The last key that was actually sent and how many seconds ago (updates every 100 ms while automation is on; stops when you disable automation).
- **Next Intention** Ã¢â‚¬â€ The next key that *would* be pressed (first ready slot in priority), with a suffix:
  - `(paused)` Ã¢â‚¬â€ Automation is off.
  - `(window)` Ã¢â‚¬â€ Automation is on but the target window (e.g. game) is not focused; keys are not sent.
  - `waiting: casting` / `waiting: channeling` Ã¢â‚¬â€ Automation is paused by cast-time safety rules.
  - `next` Ã¢â‚¬â€ Automation is on and the game is focused; this key will be sent when the minimum delay has passed.

### Automation (Left Panel)

- **Enable / Disable** Ã¢â‚¬â€ Green **Enable** turns automation on (sends keys in priority order); red **Disable** turns it off. Automation always starts **off** when you launch the app.
- **List profiles** Ã¢â‚¬â€ In **Settings Ã¢â€ â€™ Automation**, use **+ / Copy / Ã¢Ë†â€™** to create, duplicate, or remove list profiles (for example: Single Target, AoE, Utility). Each profile stores its own priority list.
- **Per-profile hotkeys** Ã¢â‚¬â€ Each list profile has two optional global binds (single key or combo like `ctrl+f24`):
  - **Toggle bind** Ã¢â‚¬â€ Switches to that profile and toggles continuous automation on/off.
  - **Single bind** Ã¢â‚¬â€ Switches to that profile and arms exactly one next action (single fire) without enabling continuous automation.
- **Conflict warning** Ã¢â‚¬â€ If a bind is reused across profiles (or toggle/single collide), Settings shows a red conflict badge so you can resolve it quickly.
- **Delay (ms)** Ã¢â‚¬â€ Minimum time in milliseconds between any two keypresses (50Ã¢â‚¬â€œ2000). Helps avoid spamming faster than the gameÃ¢â‚¬â„¢s GCD.
- **GCD (ms)** Ã¢â‚¬â€ Duration used to suppress normal priority after a queued key is sent (default 1500 ms).
- **Queue (ms)** Ã¢â‚¬â€ Extra wait after detected cast end before sending the next key (default 120 ms).
- **Allow sends while casting/channeling** Ã¢â‚¬â€ If off (default), automation waits until cast/channel completes.
- **Window title** Ã¢â‚¬â€ If you type part of the game window title here (e.g. `World of Warcraft`), keypresses are **only** sent when a window whose title contains this text (case-insensitive) is in the foreground. Leave blank to send keys regardless of focus.
- **Queue timeout / Fire delay** Ã¢â‚¬â€ Queue timeout controls how long a queued input is kept; fire delay adds a small post-ready delay before queued send.

### Other Settings

- **Always on top** Ã¢â‚¬â€ Check to keep the app window above other windows.
- **Detection** Ã¢â‚¬â€ **Polling FPS**, **Cooldown min**, **Darken**, **Trigger**, **Yellow frac**, and **Red frac** control responsiveness and cooldown/glow sensitivity.
- **Red glow threshold** Ã¢â‚¬â€ Tune **Red frac** if DoT-refresh slots are triggering too early or too late on red-glow windows.
- **Per-slot glow sensitivity (advanced)** Ã¢â‚¬â€ In `config/default_config.json`, set `detection.glow_value_delta_by_slot` (example: `{"4": 55}`) to lower/raise glow brightness delta for one slot without changing others.
- **Per-slot glow thresholds (advanced)** Ã¢â‚¬â€ Use `detection.glow_ring_fraction_by_slot` (example: `{"5": 0.08}`) and `detection.glow_override_cooldown_by_slot` (example: `[5]`) for proc-style icons that need lower yellow-fraction threshold and optional non-red glow cooldown override.
- **Cast detection** Ã¢â‚¬â€ Configure cast band %, confirmation frames, min/max cast duration, cancel grace, and channeling mode.
- **Cast bar ROI (optional)** Ã¢â‚¬â€ Define a region inside the capture box to detect active cast-bar motion; optionally mark ready slots as `locked` while active.
- **Save Settings** Ã¢â‚¬â€ Saves the current config (region, slots, keybinds, priority profiles + binds, detection, overlay, delay, window title, etc.) to `config/default_config.json`.

---

## Rebind Summary

| What to rebind | How |
|----------------|-----|
| **Slot key** (which key the app presses for that slot) | Right-click the slot Ã¢â€ â€™ **Bind Key** Ã¢â€ â€™ press the key/combo |
| **Automation list hotkeys** (global actions per profile) | In **Settings Ã¢â€ â€™ Automation**: pick the list profile, then set **Toggle bind** and/or **Single bind** |

---

## Project Structure

```
autowow/
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ README.md
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ requirements.txt
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ config/
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ default_config.json
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ src/
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ main.py              # Entry point, wiring
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ capture/             # Screen capture (mss)
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ analysis/            # Slot detection
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ overlay/             # Calibration overlay
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ ui/                  # PyQt6 control panel
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ automation/          # Key sending, global hotkey
    Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ models/              # Config and state types
```

## Requirements

- Python 3.11+
- Game in **borderless windowed** (or windowed) mode so the overlay can be drawn and the target window title can be read
- Windows recommended for the Ã¢â‚¬Å“target windowÃ¢â‚¬Â check (uses Win32 API); key sending works with the `keyboard` library on supported platforms

## Manual Actions (Not Tied to Slots)

You can now add priority entries that are not linked to a monitored icon slot.

- In the **Priority** panel, click **+ manual**.
- Enter an action name and keybind.
- The manual action is added to the current profile's priority list.
- Right-click a manual item in the priority list to **Rename**, **Rebind**, change **Ready Source**, or **Remove**.

Behavior in v1:
- Manual actions with `Ready Source: Always` are treated as eligible whenever they have a keybind.
- Manual actions with `Ready Source: Buff present` or `Buff missing` require a calibrated Buff ROI and a passing buff gate.
- They still respect automation safety gates (minimum delay, casting/channel blocking, target-window check).
- If a manual action is placed above monitored slots, it may be selected repeatedly.





## Buff ROI (New)

- Buff ROI calibration is separate from slot baseline calibration.
- In Settings -> Detection you can add buff ROIs with L/T/W/H (relative to action bar).
- Calibrate each ROI with:
  - Calibrate Present (buff visible)
- Priority slot items now support Ready Source:
  - Use slot icon state
  - Buff present
  - Buff missing
- Buff-gated readiness requires a calibrated Present template for that ROI.
- For actions using `buff_missing`, readiness requires the calibrated present template to be absent and the slot icon to be ready.
- With `Activation: DoT refresh`, buff-gated slot items must still pass buff gate; confirmed red glow only overrides slot-icon readiness.
