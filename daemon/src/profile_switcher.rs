//! Per-application hardware profile auto-switching.
//!
//! Watches the focused window and, when it changes, applies that app's
//! hardware overrides (DPI, SmartShift) on top of the user's base settings —
//! mirroring Logi Options+ / Logitune per-app profiles.
//!
//! Design notes:
//! - This reads `profiles.json` directly with a *tolerant* parser that only
//!   extracts each profile's window class and optional `device` overrides. It
//!   deliberately ignores the radial-menu `slices` (whose on-disk shape differs
//!   between the daemon and the Settings UI), so the two stay decoupled.
//! - Overrides are *partial*: a profile only sets the fields it cares about
//!   (e.g. just DPI). Unset fields fall back to a one-time snapshot of the
//!   user's base settings, so switching to an app without a binding (or one
//!   that only tweaks DPI) cleanly restores everything else.
//! - Window-class detection is X11-first (via `xdotool`, already a runtime
//!   dependency for key synthesis). Wayland/KWin focus tracking is a follow-up;
//!   on unsupported sessions the loop simply never resolves a class and stays
//!   idle, so nothing breaks.

use std::path::Path;
use std::time::{Duration, SystemTime};

use serde::Deserialize;

use crate::config::SharedConfig;
use crate::hidpp::SharedHapticManager;
use crate::profiles::{self, DeviceSettings};

/// How often to poll the focused window.
const POLL_INTERVAL: Duration = Duration::from_millis(500);

/// Applied-state key used when no app-specific override is in effect.
const BASE_KEY: &str = "\u{0}base";

/// Window classes that should never drive a profile switch (shells, launchers).
const IGNORED_CLASSES: &[&str] = &[
    "",
    "plasmashell",
    "org.kde.plasmashell",
    "kwin_wayland",
    "kwin_x11",
    "org.kde.krunner",
    "krunner",
    "gnome-shell",
    "org.gnome.shell",
];

/// Minimal view of a profile entry in `profiles.json` (slices etc. ignored).
#[derive(Debug, Deserialize)]
struct RawProfile {
    #[serde(default, alias = "app_class")]
    window_class: Option<String>,
    #[serde(default)]
    device: Option<DeviceSettings>,
}

/// One-time snapshot of the user's base hardware settings, so apps without an
/// override (or that only override some settings) restore the rest.
#[derive(Debug, Clone, Default)]
struct BaseSettings {
    /// Pointer DPI
    dpi: Option<u16>,
    /// SmartShift as (enabled, device_threshold) — the form `set_smart_shift` takes
    smartshift: Option<(bool, u8)>,
}

/// Convert a UI sensitivity percentage (1..100) to the device threshold,
/// matching the Settings UI (`device = (100 - ui) * 2.55`).
fn ui_to_device_threshold(ui: u8) -> u8 {
    let ui = ui.clamp(1, 100) as f32;
    ((100.0 - ui) * 2.55).round().clamp(0.0, 255.0) as u8
}

/// Whether a window class should be ignored.
fn is_ignored_class(class: &str) -> bool {
    IGNORED_CLASSES.iter().any(|c| c.eq_ignore_ascii_case(class))
}

/// Last-modified time of a file, or None if it can't be stat'd.
fn file_mtime(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Load per-app hardware overrides from `profiles.json`.
///
/// Tolerates both the typed `{version, profiles: [...]}` schema and the flat
/// `{name: {...}}` map the Settings UI writes. Returns `(lowercase window
/// class, overrides)` pairs, skipping profiles with no hardware overrides.
fn load_hw_profiles(path: &Path) -> Vec<(String, DeviceSettings)> {
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };

    let value: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!(error = %e, "profiles.json is not valid JSON, per-app switching disabled");
            return Vec::new();
        }
    };

    // Gather candidate profile objects from BOTH schemas. A real file can
    // contain both at once: the daemon seeds the typed {version, profiles: []}
    // shape, and the Settings UI then appends flat {name: {...}} keys next to
    // it. So we scan the "profiles" array AND the other top-level object values,
    // skipping the structural "version"/"profiles" keys.
    let mut candidates: Vec<serde_json::Value> = Vec::new();
    if let Some(arr) = value.get("profiles").and_then(|p| p.as_array()) {
        candidates.extend(arr.iter().cloned());
    }
    if let Some(obj) = value.as_object() {
        for (key, v) in obj {
            if key == "version" || key == "profiles" {
                continue;
            }
            candidates.push(v.clone());
        }
    }

    let mut out = Vec::new();
    for v in candidates {
        if !v.is_object() {
            continue; // skip scalars like a top-level "version"
        }
        // Unknown fields (slices, name, icon, ...) are ignored by RawProfile.
        if let Ok(rp) = serde_json::from_value::<RawProfile>(v) {
            if let (Some(class), Some(device)) = (rp.window_class, rp.device) {
                if !device.is_empty() {
                    out.push((class.to_lowercase(), device));
                }
            }
        }
    }
    out
}

/// Find the override for a window class (case-insensitive).
fn resolve<'a>(
    profiles: &'a [(String, DeviceSettings)],
    class: &str,
) -> Option<&'a DeviceSettings> {
    let lc = class.to_lowercase();
    profiles.iter().find(|(c, _)| *c == lc).map(|(_, d)| d)
}

/// Query the focused window's class (X11). Returns lowercase class or None.
async fn active_window_class() -> Option<String> {
    let output = tokio::process::Command::new("xdotool")
        .args(["getactivewindow", "getwindowclassname"])
        .output()
        .await
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let class = String::from_utf8_lossy(&output.stdout)
        .trim()
        .to_lowercase();

    if class.is_empty() {
        None
    } else {
        Some(class)
    }
}

/// Snapshot the device's current DPI/SmartShift as the base to restore to.
fn snapshot_base(haptic_manager: &SharedHapticManager) -> BaseSettings {
    let mut base = BaseSettings::default();
    if let Ok(mut manager) = haptic_manager.lock() {
        base.dpi = manager.get_dpi();
        base.smartshift = manager.get_smart_shift();
    }
    tracing::info!(
        dpi = ?base.dpi,
        smartshift = ?base.smartshift,
        "Captured base hardware settings for per-app profiles"
    );
    base
}

/// Apply the effective settings (base overlaid with the profile's overrides).
fn apply_effective(
    haptic_manager: &SharedHapticManager,
    base: &BaseSettings,
    over: Option<&DeviceSettings>,
) {
    let mut manager = match haptic_manager.lock() {
        Ok(m) => m,
        Err(e) => {
            tracing::warn!(error = %e, "Cannot lock haptic manager to apply profile");
            return;
        }
    };

    // DPI: override or base.
    if let Some(dpi) = over.and_then(|o| o.dpi).or(base.dpi) {
        if let Err(e) = manager.set_dpi(dpi) {
            tracing::debug!(error = %e, dpi, "Failed to apply DPI");
        }
    }

    // SmartShift: if the profile overrides either field, merge with base for
    // the other; otherwise restore the base wholesale.
    let overrides_smartshift = over
        .map(|o| o.smartshift_enabled.is_some() || o.smartshift_threshold.is_some())
        .unwrap_or(false);

    let smartshift = if overrides_smartshift {
        let o = over.unwrap();
        let enabled = o
            .smartshift_enabled
            .or(base.smartshift.map(|(e, _)| e))
            .unwrap_or(true);
        let threshold = match o.smartshift_threshold {
            Some(ui) => ui_to_device_threshold(ui),
            None => base.smartshift.map(|(_, t)| t).unwrap_or(128),
        };
        Some((enabled, threshold))
    } else {
        base.smartshift
    };

    if let Some((enabled, threshold)) = smartshift {
        if let Err(e) = manager.set_smart_shift(enabled, threshold) {
            tracing::debug!(error = %e, "Failed to apply SmartShift");
        }
    }
}

/// Run the per-app profile switcher loop (spawned as a background task).
pub async fn run_profile_switcher(
    haptic_manager: SharedHapticManager,
    _shared_config: SharedConfig,
) {
    let path = profiles::get_profiles_path();
    let mut mtime = file_mtime(&path);
    let mut hw_profiles = load_hw_profiles(&path);

    let mut base: Option<BaseSettings> = None;
    // Key of the currently applied state: a window class, or BASE_KEY.
    let mut applied_key = String::new();

    tracing::info!(
        bindings = hw_profiles.len(),
        "Per-app hardware profile switcher started"
    );

    loop {
        tokio::time::sleep(POLL_INTERVAL).await;

        // Hot-reload bindings when profiles.json changes (user edits the UI).
        let new_mtime = file_mtime(&path);
        if new_mtime != mtime {
            mtime = new_mtime;
            hw_profiles = load_hw_profiles(&path);
            applied_key.clear(); // force re-evaluation against the new set
            tracing::info!(bindings = hw_profiles.len(), "Per-app profiles reloaded");
        }

        // No hardware bindings at all -> nothing to do this tick.
        if hw_profiles.is_empty() {
            continue;
        }

        let class = match active_window_class().await {
            Some(c) => c,
            None => continue,
        };

        if is_ignored_class(&class) {
            continue;
        }

        let over = resolve(&hw_profiles, &class);
        // State key: the bound class, or a shared sentinel for "no override".
        let key = if over.is_some() { class.as_str() } else { BASE_KEY };
        if key == applied_key {
            continue;
        }

        // Capture the base lazily, just before the first switch, so it reflects
        // the user's running configuration.
        if base.is_none() {
            base = Some(snapshot_base(&haptic_manager));
        }

        apply_effective(&haptic_manager, base.as_ref().unwrap(), over);
        tracing::info!(class = %class, override_active = over.is_some(), "Applied per-app hardware profile");
        applied_key = key.to_string();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_ui_to_device_threshold() {
        // Matches the Settings UI conversion device = (100 - ui) * 2.55
        assert_eq!(ui_to_device_threshold(100), 0);
        assert_eq!(ui_to_device_threshold(1), 252); // (99 * 2.55).round()
        assert_eq!(ui_to_device_threshold(50), 128); // (50 * 2.55).round()
    }

    #[test]
    fn test_is_ignored_class() {
        assert!(is_ignored_class(""));
        assert!(is_ignored_class("plasmashell"));
        assert!(is_ignored_class("KWin_x11")); // case-insensitive
        assert!(!is_ignored_class("firefox"));
        assert!(!is_ignored_class("steam_app_220"));
    }

    fn write_tmp(json: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        f.write_all(json.as_bytes()).unwrap();
        f
    }

    #[test]
    fn test_load_flat_dict_schema() {
        // The flat {name: profile} map the Settings UI writes, with app_class.
        let json = r#"{
            "firefox": {
                "name": "firefox",
                "app_class": "firefox",
                "slices": [null, null, null, null, null, null, null, null],
                "device": {"dpi": 800}
            },
            "game": {
                "name": "game",
                "app_class": "Steam",
                "device": {"smartshift_enabled": false, "smartshift_threshold": 90}
            },
            "menuonly": {"name": "menuonly", "app_class": "code", "slices": []}
        }"#;
        let f = write_tmp(json);
        let profiles = load_hw_profiles(f.path());

        // menuonly has no device override -> excluded
        assert_eq!(profiles.len(), 2);
        let ff = resolve(&profiles, "Firefox").unwrap();
        assert_eq!(ff.dpi, Some(800));
        let game = resolve(&profiles, "steam").unwrap();
        assert_eq!(game.smartshift_enabled, Some(false));
        assert_eq!(game.smartshift_threshold, Some(90));
        assert!(resolve(&profiles, "unbound").is_none());
    }

    #[test]
    fn test_load_hybrid_schema() {
        // The real file shape the Settings UI produces: the daemon's typed
        // {version, profiles:[default]} plus a flat per-app key appended next
        // to it. Both must be picked up.
        let json = r#"{
            "version": 1,
            "profiles": [
                {"name": "default", "slices": [null,null,null,null,null,null,null,null]}
            ],
            "konsole": {
                "name": "konsole",
                "app_class": "konsole",
                "slices": [],
                "device": {"dpi": 1000, "smartshift_enabled": true, "smartshift_threshold": 50}
            }
        }"#;
        let f = write_tmp(json);
        let profiles = load_hw_profiles(f.path());
        assert_eq!(profiles.len(), 1);
        let k = resolve(&profiles, "konsole").unwrap();
        assert_eq!(k.dpi, Some(1000));
        assert_eq!(k.smartshift_threshold, Some(50));
    }

    #[test]
    fn test_load_typed_schema() {
        // The typed {version, profiles: [...]} schema, with window_class.
        let json = r#"{
            "version": 1,
            "profiles": [
                {"name": "default", "slices": [null,null,null,null,null,null,null,null]},
                {"name": "ff", "window_class": "firefox", "device": {"dpi": 1600},
                 "slices": [null,null,null,null,null,null,null,null]}
            ]
        }"#;
        let f = write_tmp(json);
        let profiles = load_hw_profiles(f.path());
        assert_eq!(profiles.len(), 1);
        assert_eq!(resolve(&profiles, "firefox").unwrap().dpi, Some(1600));
    }
}
