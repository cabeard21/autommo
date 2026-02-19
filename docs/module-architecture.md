# ChillDown Module Architecture

## Overview

The module system lets self-contained feature packages build upon each other. A module can depend on another module, consume its data, extend its UI, and hook into its lifecycle. The core app is just a thin shell that manages modules and provides shared services.

---

## Module Anatomy

```
modules/
  cooldown_rotation/
    __init__.py              # exports CooldownRotationModule
    module.py                # module class
    settings_tab.py          # settings UI (optional)
    worker.py                # per-frame logic (optional)
    services.py              # services this module exposes (optional)
    
  health_monitor/
    __init__.py
    module.py
    settings_tab.py
    worker.py

  buff_tracker/
    __init__.py
    module.py
    settings_tab.py
```

---

## Module Declaration

Every module declares who it is, what it needs, and what it offers:

```python
class CooldownRotationModule(BaseModule):
    # Identity
    name = "Cooldown Rotation"          # human-readable, shows as tab name
    key = "cooldown_rotation"           # machine key, config namespace, unique ID
    version = "1.0.0"
    description = "Monitors action bar cooldowns and sends keys based on priority"
    
    # Dependencies
    requires = []                       # module keys this MUST have to function
    optional = []                       # module keys this CAN use if present
    
    # What this module exposes to others
    provides_services = [
        "slot_states",                  # other modules can read current slot states
        "priority_order",               # other modules can read/modify priority
        "gcd_estimate",                 # other modules can read estimated GCD timing
    ]
    
    # Extension points: named slots where other modules can inject
    extension_points = [
        "settings.detection",           # inject widgets into detection settings
        "settings.automation",          # inject widgets into automation settings
        "priority.evaluate",            # inject logic into priority evaluation
    ]
    
    # Hooks this module emits (events other modules can subscribe to)
    hooks = [
        "slot_states_updated",          # fired each frame with new slot states
        "key_sent",                     # fired when a key is actually sent
        "rotation_toggled",             # fired when automation enabled/disabled
    ]
```

```python
class HealthMonitorModule(BaseModule):
    name = "Health Monitor"
    key = "health_monitor"
    version = "1.0.0"
    description = "Monitors player health bar and can override rotation for emergency heals"
    
    requires = []                       # works standalone for just health display
    optional = ["cooldown_rotation"]    # if present, can override rotation
    
    provides_services = [
        "health_pct",                   # current health percentage
        "health_zone",                  # "safe", "caution", "danger", "critical"
    ]
    
    extension_points = [
        "settings.thresholds",          # other modules could inject custom zones
    ]
    
    hooks = [
        "health_changed",              # fired when health zone transitions
        "emergency_triggered",          # fired when health drops below critical
    ]
```

---

## Core Services (provided by the app, not by any module)

```python
class Core:
    """
    Passed to every module's setup(). Provides shared infrastructure.
    Modules never import each other directly — they go through Core.
    """
    
    # === Shared Infrastructure ===
    
    def get_config(self, module_key: str) -> dict:
        """Get a module's namespaced config section."""
        
    def save_config(self, module_key: str, data: dict):
        """Save a module's config section. Triggers auto-save."""
    
    def get_capture(self) -> ScreenCapture:
        """Access the shared screen capture system."""
        
    def get_key_sender(self) -> KeySender:
        """Access the shared key sender (window check, interval, etc.)."""
    
    # === Module Discovery ===
    
    def get_module(self, key: str) -> BaseModule | None:
        """Get a reference to another loaded module. Returns None if not loaded."""
        
    def is_module_loaded(self, key: str) -> bool:
        """Check if a module is available."""
    
    # === Services (data other modules expose) ===
    
    def get_service(self, module_key: str, service_name: str) -> Any:
        """
        Read a service value from another module.
        e.g. core.get_service("cooldown_rotation", "slot_states")
        Returns None if the module isn't loaded or service doesn't exist.
        """
    
    # === Extensions (UI injection) ===
    
    def register_extension(self, point: str, widget_factory: Callable) -> None:
        """
        Register a widget factory for an extension point.
        The owning module calls get_extensions() when building its UI.
        widget_factory is a callable that returns a QWidget.
        
        e.g. core.register_extension(
            "cooldown_rotation.settings.detection", 
            self.build_health_override_widget
        )
        """
        
    def get_extensions(self, point: str) -> list[QWidget]:
        """
        Called by the module that owns the extension point.
        Returns widgets registered by other modules.
        
        e.g. in CooldownRotationModule.get_settings_widget():
            for widget in core.get_extensions("cooldown_rotation.settings.detection"):
                layout.addWidget(widget)
        """
    
    # === Hooks (events) ===
    
    def subscribe(self, hook: str, callback: Callable) -> None:
        """
        Subscribe to a hook emitted by another module.
        Hook names are namespaced: "{module_key}.{hook_name}"
        
        e.g. core.subscribe("cooldown_rotation.key_sent", self.on_key_sent)
        """
    
    def emit(self, hook: str, **kwargs) -> None:
        """
        Emit a hook. Only the module that declared the hook should emit it.
        
        e.g. core.emit("cooldown_rotation.slot_states_updated", states=slot_states)
        """
```

---

## BaseModule Lifecycle

```python
class BaseModule(ABC):
    """Base class all modules inherit from."""
    
    # --- Subclass must define ---
    name: str
    key: str
    version: str = "1.0.0"
    description: str = ""
    requires: list[str] = []
    optional: list[str] = []
    provides_services: list[str] = []
    extension_points: list[str] = []
    hooks: list[str] = []
    
    def setup(self, core: Core) -> None:
        """
        Called once after all modules are loaded and dependency-checked.
        Store core reference, subscribe to hooks, register extensions.
        This is where you do: self.core = core
        Do NOT access other modules' services here — they may not be setup yet.
        """
        pass
    
    def ready(self) -> None:
        """
        Called once after ALL modules have completed setup().
        Safe to access other modules' services here.
        This is where you read initial state from dependencies.
        """
        pass
    
    def get_settings_widget(self) -> QWidget | None:
        """
        Return a QWidget to be added as a tab in the settings dialog.
        Return None if this module has no settings.
        The widget should use shared QSS theme variables for consistent styling.
        """
        return None
    
    def get_status_widget(self) -> QWidget | None:
        """
        Return a small QWidget to show in the main window's module status area.
        e.g. a health bar, a buff list, a rotation state indicator.
        Return None if nothing to show in the main window.
        """
        return None
    
    def get_service_value(self, service_name: str) -> Any:
        """
        Called by Core when another module requests one of this module's services.
        Return the current value for the named service.
        
        e.g. if service_name == "slot_states": return self.current_slot_states
        """
        return None
    
    def on_enable(self) -> None:
        """Module-specific activation logic."""
        pass
    
    def on_disable(self) -> None:
        """Module-specific deactivation logic."""
        pass
    
    def on_config_changed(self, key: str, value: Any) -> None:
        """Called when any of this module's config values change."""
        pass
    
    def on_frame(self, frame) -> None:
        """
        Called each capture cycle with the raw captured frame.
        Only implement if the module needs per-frame processing.
        Modules process frames in dependency order (dependencies first).
        """
        pass
    
    def teardown(self) -> None:
        """Cleanup on app shutdown. Unsubscribe, release resources."""
        pass
```

---

## ModuleManager

```python
class ModuleManager:
    """
    Discovers, loads, validates, and manages modules.
    """
    
    def __init__(self, core: Core):
        self.core = core
        self.modules: dict[str, BaseModule] = {}   # key -> instance
        self._load_order: list[str] = []            # topologically sorted
    
    def discover(self, modules_dir: str) -> list[str]:
        """
        Scan the modules directory for valid module packages.
        A valid package has __init__.py that exports a BaseModule subclass.
        Returns list of discovered module keys.
        """
    
    def load(self, module_keys: list[str] | None = None) -> None:
        """
        Load specified modules (or all discovered).
        1. Instantiate each module class
        2. Validate dependencies (requires/optional)
        3. Topological sort by dependency graph
        4. Call setup() in dependency order
        5. Call ready() in dependency order (after all setup() complete)
        """
    
    def get(self, key: str) -> BaseModule | None:
        """Get a loaded module by key."""
        return self.modules.get(key)
    
    def process_frame(self, frame) -> None:
        """
        Called each capture cycle.
        Calls on_frame() on each module in dependency order.
        Dependencies process first so their services are up-to-date
        when downstream modules read them.
        """
        for key in self._load_order:
            module = self.modules[key]
            if module.enabled:
                module.on_frame(frame)
    
    def get_settings_widgets(self) -> list[tuple[str, QWidget]]:
        """
        Returns (tab_name, widget) pairs for all modules that have settings.
        Used by the settings dialog to build tabs.
        """
        result = []
        for key in self._load_order:
            module = self.modules[key]
            widget = module.get_settings_widget()
            if widget:
                result.append((module.name, widget))
        return result
    
    def get_status_widgets(self) -> list[tuple[str, QWidget]]:
        """
        Returns (module_name, widget) pairs for main window display.
        """
        result = []
        for key in self._load_order:
            module = self.modules[key]
            widget = module.get_status_widget()
            if widget:
                result.append((module.name, widget))
        return result
```

---

## Dependency Resolution

```
# Example dependency graph:
#
#   cooldown_rotation (requires: nothing)
#        ↑
#   health_monitor (optional: cooldown_rotation)
#        ↑
#   auto_heal (requires: health_monitor, cooldown_rotation)
#
# Load order: cooldown_rotation → health_monitor → auto_heal
# Frame processing order: same (dependencies update first)
```

Rules:
- `requires`: module MUST be present or loading fails with a clear error
- `optional`: module is used if present, gracefully ignored if not
- Circular dependencies are an error at load time
- Topological sort determines load order AND frame processing order
- If a required dependency is missing, the dependent module is skipped (not loaded) with a warning, not a crash

---

## Config Namespacing

```json
{
  "core": {
    "monitor": 2,
    "capture_region": { "top": 849, "left": 757, "width": 408, "height": 36 },
    "display": { "history_rows": 3, "always_on_top": true },
    "modules_enabled": ["cooldown_rotation", "health_monitor"]
  },
  "cooldown_rotation": {
    "detection_region": "top_left",
    "trigger_threshold": 0.4,
    "darken_threshold": 30,
    "priority_order": [0, 1, 2],
    "keybinds": { "0": "1", "1": "2", "2": "3" },
    "queue_whitelist": ["v"],
    "queue_timeout_ms": 5000
  },
  "health_monitor": {
    "health_bar_region": { "top": 200, "left": 100, "width": 250, "height": 20 },
    "low_health_threshold": 30,
    "critical_health_threshold": 15,
    "emergency_heal_key": "F1"
  }
}
```

Each module reads/writes only its own section through `core.get_config(self.key)` and `core.save_config(self.key, data)`. Modules never touch another module's config — they interact through services and hooks.

---

## Interaction Patterns

### Pattern 1: Reading Another Module's Data

```python
# Health monitor reads slot states from cooldown rotation
class HealthMonitorModule(BaseModule):
    optional = ["cooldown_rotation"]
    
    def ready(self):
        # Check if cooldown rotation is available
        if self.core.is_module_loaded("cooldown_rotation"):
            self.has_rotation = True
    
    def on_frame(self, frame):
        self.update_health(frame)
        
        if self.has_rotation and self.health_zone == "critical":
            # Read current slot states to know if we can interrupt
            states = self.core.get_service("cooldown_rotation", "slot_states")
            if states:
                self.plan_emergency_action(states)
```

### Pattern 2: Reacting to Events

```python
# Buff tracker reacts when cooldown rotation sends a key
class BuffTrackerModule(BaseModule):
    optional = ["cooldown_rotation"]
    
    def setup(self, core):
        self.core = core
        if core.is_module_loaded("cooldown_rotation"):
            core.subscribe("cooldown_rotation.key_sent", self.on_key_sent)
    
    def on_key_sent(self, key: str, slot_index: int, **kwargs):
        # Track which abilities were used for buff timing
        self.record_ability_use(slot_index)
```

### Pattern 3: Injecting UI into Another Module

```python
# Health monitor injects a "Health Emergency Override" section
# into cooldown rotation's automation settings
class HealthMonitorModule(BaseModule):
    optional = ["cooldown_rotation"]
    
    def setup(self, core):
        self.core = core
        if core.is_module_loaded("cooldown_rotation"):
            core.register_extension(
                "cooldown_rotation.settings.automation",
                self._build_override_widget
            )
    
    def _build_override_widget(self) -> QWidget:
        """Builds a section that appears inside cooldown rotation's settings tab."""
        section = QFrame()
        section.setObjectName("section")
        layout = QVBoxLayout(section)
        title = QLabel("HEALTH EMERGENCY OVERRIDE")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        # ... add threshold spinbox, enable checkbox, heal key bind, etc.
        # Wire to self.core.get_config("health_monitor") for persistence
        return section
```

```python
# Cooldown rotation leaves room for injected widgets
class CooldownRotationModule(BaseModule):
    extension_points = ["settings.detection", "settings.automation"]
    
    def get_settings_widget(self):
        tabs = QTabWidget()
        
        # Detection tab
        detection = QWidget()
        det_layout = QVBoxLayout(detection)
        det_layout.addWidget(self._build_threshold_section())
        det_layout.addWidget(self._build_glow_section())
        # Extension point: other modules inject here
        for widget_factory in self.core.get_extensions(f"{self.key}.settings.detection"):
            det_layout.addWidget(widget_factory())
        det_layout.addStretch()
        tabs.addTab(detection, "Detection")
        
        # Automation tab
        automation = QWidget()
        auto_layout = QVBoxLayout(automation)
        auto_layout.addWidget(self._build_controls_section())
        auto_layout.addWidget(self._build_timing_section())
        auto_layout.addWidget(self._build_queue_section())
        # Extension point: other modules inject here
        for widget_factory in self.core.get_extensions(f"{self.key}.settings.automation"):
            auto_layout.addWidget(widget_factory())
        auto_layout.addStretch()
        tabs.addTab(automation, "Automation")
        
        return tabs
```

### Pattern 4: Modifying Another Module's Behavior

```python
# Health monitor temporarily overrides cooldown rotation priority
class HealthMonitorModule(BaseModule):
    optional = ["cooldown_rotation"]
    
    def on_frame(self, frame):
        self.update_health(frame)
        
        if self.health_zone == "critical" and self.has_rotation:
            # Tell cooldown rotation to prioritize heal spell
            rotation = self.core.get_module("cooldown_rotation")
            if rotation:
                rotation.set_temporary_override(
                    key=self.config["emergency_heal_key"],
                    reason="health_emergency",
                    duration_ms=2000
                )
            self.core.emit("health_monitor.emergency_triggered", 
                          health_pct=self.health_pct)
```

This works because `set_temporary_override` is a public method on CooldownRotationModule that any module can call through `core.get_module()`. It's not an extension point or a hook — it's just a normal method call. The owning module decides what methods it makes public.

---

## Settings Dialog Integration

The settings dialog becomes a thin shell:

```python
class SettingsDialog(QDialog):
    def __init__(self, core, module_manager):
        super().__init__()
        self.tabs = QTabWidget()
        
        # Core tab (always present): General settings
        self.tabs.addTab(self._build_general_tab(), "General")
        
        # Module tabs (dynamic): each module contributes its own tab
        for name, widget in module_manager.get_settings_widgets():
            self.tabs.addTab(widget, name)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self._build_status_bar())
```

If a module has sub-tabs (like cooldown rotation having Detection and Automation), the module returns a QTabWidget as its widget — tabs within tabs. Or it returns a single scrollable widget. The settings dialog doesn't care.

---

## Main Window Integration

```python
class MainWindow(QMainWindow):
    def __init__(self, core, module_manager):
        # ... existing layout ...
        
        # Module status area: below the existing controls
        self.module_status_area = QVBoxLayout()
        for name, widget in module_manager.get_status_widgets():
            self.module_status_area.addWidget(widget)
```

Each module's status widget shows whatever is relevant to gameplay — the cooldown rotation module shows slot states + priority list + last action + next intention (what the main window shows now). A health module might show a health bar. A buff tracker might show active buff timers.

---

## What Stays Core vs. What Becomes a Module

### Core (shared infrastructure, always present)
- App shell (window management, tray, startup/shutdown)
- Screen capture system (ScreenCapture class)
- Key sender system (KeySender class)  
- Config system (load/save/namespace)
- Settings dialog shell (tab container + General tab + status bar)
- Main window shell (layout container + module widget areas)
- Module manager (discovery, loading, lifecycle)
- Theme/QSS system
- Global hotkey listener

### Module: cooldown_rotation
- SlotAnalyzer (detection logic, baselines, glow, quadrant)
- Priority list management
- Automation loop (evaluate_and_send)
- Queue listener + queue override logic
- Cast/channel detection
- Its settings tabs (Detection + Automation)
- Its main window widgets (slot states, live preview, last action, next intention, priority list)

### Module: health_monitor (future)
- Health bar region capture
- Health percentage calculation  
- Zone detection (safe/caution/danger/critical)
- Emergency override integration with cooldown_rotation (optional dep)
- Its settings tab
- Its main window widget (health bar display)

### Core → General Settings Tab
- Profile (name, export, import)
- Display (monitor, overlay, always on top, history rows)
- Capture region (this is shared infrastructure — modules use it or define their own regions)
- Calibration (this might move INTO cooldown_rotation since it's slot-specific)

---

## Discovery & Loading Flow

```
App Start
  ├─ Load core config
  ├─ Initialize Core services (capture, key_sender, config)
  ├─ ModuleManager.discover("./modules/")
  │    └─ Finds: cooldown_rotation, health_monitor, buff_tracker
  ├─ Check core.config["modules_enabled"]
  │    └─ Enabled: ["cooldown_rotation", "health_monitor"]
  ├─ ModuleManager.load(["cooldown_rotation", "health_monitor"])
  │    ├─ Instantiate both modules
  │    ├─ Validate dependencies (all satisfied)
  │    ├─ Topological sort: cooldown_rotation → health_monitor
  │    ├─ cooldown_rotation.setup(core)  ← subscribes, registers extensions
  │    ├─ health_monitor.setup(core)     ← subscribes, registers extensions
  │    ├─ cooldown_rotation.ready()      ← reads initial state
  │    └─ health_monitor.ready()         ← reads rotation state if available
  ├─ Build settings dialog (General + module tabs)
  ├─ Build main window (core layout + module status widgets)
  └─ Start capture loop
       └─ Each frame:
            ├─ Capture screen
            ├─ cooldown_rotation.on_frame(frame)  ← processes first (no deps)
            │    ├─ Analyze slots
            │    ├─ Evaluate priority
            │    ├─ Send key if needed
            │    └─ core.emit("cooldown_rotation.slot_states_updated", ...)
            └─ health_monitor.on_frame(frame)      ← processes second
                 ├─ Analyze health bar
                 ├─ Read rotation slot states if needed
                 └─ Trigger emergency if critical
```

---

## Summary of Interaction Mechanisms

| Mechanism | Purpose | Example |
|-----------|---------|---------|
| **Services** | Read data from another module | health_monitor reads slot_states from cooldown_rotation |
| **Hooks** | React to events from another module | buff_tracker reacts to cooldown_rotation.key_sent |
| **Extensions** | Inject UI widgets into another module's settings | health_monitor adds override config inside cooldown_rotation's tab |
| **Direct method calls** | Trigger specific behavior on another module | health_monitor calls rotation.set_temporary_override() |
| **Config** | Persistent per-module settings | each module reads/writes its own namespaced section |

Modules never import each other. All interaction goes through Core. If a dependency isn't loaded, the dependent module gracefully degrades (optional deps) or doesn't load (required deps).
