The cleanest way to support “transform/stance/metamorphosis” classes (where **some** buttons flip to new icons and those new icons have their own cooldown visuals) is to treat the UI as having **multiple visual “forms”** and make your detector/automation **form-aware**, instead of trying to force everything into one baseline per slot.

This fits your current baseline-driven approach and your existing “gates” concept (priority profiles, per-slot rules, optional Buff ROI gating, etc.). 

## Recommended approach: Forms + per-slot icon variants

### 1) Introduce a `Form` concept (Normal, Transformed, etc.)

* `form_id`: `"normal"`, `"meta"`, `"shadowform"`, …
* A “form” is just: *which set of icon appearances should be considered the “ready baseline” for each slot right now.*

### 2) Detect the active form explicitly (don’t infer it indirectly from cooldowns)

Best option (most robust): **Form Detector ROI**

* Reuse your Buff ROI idea but designate one ROI as the **form indicator**:

  * e.g., a stance buff icon, a metamorphosis buff, a class resource frame change, etc.
* Output: `active_form_id` each frame with a little hysteresis (see below).

Fallback option: **Anchor-slot matching**

* Choose 1–3 slots that *definitely change* on transform (anchors).
* For those slots, keep both the normal and transformed baselines and decide the form by “which baseline matches better”.

In practice, ROI-based form detection is vastly more stable than trying to infer from per-slot cooldown darkness, because cooldown visuals can look like “not baseline” too.

### 3) Store baselines per slot *per form*

Instead of:

* `baseline[slot_i]`

Use:

* `baseline[form_id][slot_i]` (only required for slots that visually differ in that form)

So if only 5 of 12 icons change in form, you only need transformed baselines for those 5; the others fall back to normal.

### 4) Make slot analysis pick the correct baseline based on the active form

Per frame:

1. Determine `active_form_id`
2. For each slot:

   * `baseline = baseline[active_form_id].get(slot_i) or baseline["normal"][slot_i]`
   * Compare current slot image to that baseline to decide ready/cooldown/glow/etc.

That’s it — your existing brightness/histogram delta logic stays intact; it just uses the right reference image.

---

## Cooldown tracking: don’t let form-flips erase your memory

Transforms often happen mid-combat while things are on cooldown. If you naïvely switch baselines, you can get a few frames of “???” that may look like “ready” or “cooldown” incorrectly.

The fix: maintain **cooldown memory** per *physical input* (slot/keybind), and smooth transitions.

### A practical rule

Track a per-slot “cooldown model” that is independent of form:

* If a slot was confidently **on cooldown** a moment ago, and then form changes:

  * keep it “on cooldown” until you see strong evidence it’s ready again
* Add a short “transition settle” window after a form change (e.g., 150–300ms) where you:

  * freeze the slot state, or
  * require 2–3 consecutive frames before changing state

This prevents “flip → icon changed → detector thinks it’s ready → automation spams”.

### Shared vs separate cooldowns across forms

Some games have:

* **shared cooldown per keybind** (same slot, different spell)
* **separate cooldowns** (two different abilities depending on form)

Support both with one config field on the slot:

* `cooldown_group_id` (default: the slot index or keybind)

  * if shared: both form-variants point to same group id
  * if separate: give each variant its own group id

---

## Automation / Priority list: add “form conditions” instead of duplicating profiles everywhere

Right now your priority items map to a slot and have activation rules, optional glow requirements, and optional Buff ROI gates. 

To handle transforms cleanly:

### Add one optional field to each priority item:

* `required_form`: `null | "normal" | "meta" | …`

Then:

* Normal abilities: `required_form = null` (works always)
* Transformed-only abilities: `required_form = "meta"`

This lets you keep **one profile** (e.g., “Single Target”) that includes both normal and transformed actions, and the engine simply ignores entries that don’t match the current form.

If you prefer a “single entry that means ‘same key, different spell’”, you can also support:

* `slot_ref = (slot_i)` plus `required_form` as above
  …and just add *two* entries (one normal, one transformed) pointing at the same keybind.

---

## Calibration UX that won’t annoy you

Add a form-aware calibration flow:

1. Calibrate Normal baselines (what you already do)
2. Transform in-game
3. “Calibrate baselines for current form”

   * only capture slots that are visibly different (optional: detect which slots changed by similarity threshold)
4. Save

Nice-to-have: **Auto-learn alternate baselines**

* If the user toggles “Calibration Mode” and you observe a slot settle into a *new stable appearance* for N frames, store it as an alternate baseline for the current form.
* (This matches your broader “calibration mode that records significant changes” idea.)

---

## Minimal data model sketch (JSON-ish)

```json
{
  "forms": [
    {"id": "normal", "name": "Normal"},
    {"id": "meta", "name": "Metamorphosis"}
  ],
  "form_detector": {
    "type": "buff_roi",
    "roi_id": "meta_buff",
    "present_form": "meta",
    "absent_form": "normal",
    "confirm_frames": 3,
    "settle_ms": 200
  },
  "baselines": {
    "normal": {"0": "...", "1": "..."},
    "meta":   {"3": "...", "4": "..."}   // only slots that change
  },
  "priority_items": [
    {"slot": 3, "required_form": "normal", "activation": "always"},
    {"slot": 3, "required_form": "meta",   "activation": "always"}
  ]
}
```

---

## Edge cases to explicitly handle

* **Partial swaps**: only some icons change → allow per-slot baseline fallback.
* **Transform animation**: a few frames are “in between” → settle window + confirm frames.
* **Proc glows in either form**: glow detection should be applied relative to whichever baseline is active.
* **UI paging vs transformation**: some games also page the action bar. If you support paging, treat it similarly: “page” is another dimension like “form”.

---

If you want the “best bang for buck” implementation order:

1. Add `FormDetector` (Buff ROI-based) + `active_form_id` with hysteresis
2. Add per-form baselines with fallback to normal
3. Add `required_form` on priority items
4. Add cooldown memory smoothing across form switches

That will get you stable transform handling without rewriting your whole analyzer.
