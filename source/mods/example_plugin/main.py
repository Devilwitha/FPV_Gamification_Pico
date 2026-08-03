"""example_plugin - minimales Beispiel-Plugin.

Zeigt beide Bausteine des Plugin-Systems an einem einfachen Fall:
1. Lifecycle (setup/loop/teardown) - hier nur ein Heartbeat-Zaehler.
2. ui_slots - render_system_slot() blendet den Zaehler zusaetzlich unten in
   den bestehenden "System"-Tab der Pico-Weboberflaeche ein (siehe
   manifest.json's ui_slots-Eintrag und admin_system.html's
   <!--PLUGIN_SLOT:system--> Marker).

Siehe template/README.md fuer die vollstaendige Erklaerung aller Hooks.
"""

_heartbeat_count = 0
_debug_log = None


def setup(context):
    """Wird einmalig beim (Re-)Aktivieren des Plugins aufgerufen. Eine
    Exception hier markiert das Plugin sofort als has_error=True und
    verhindert, dass loop() jemals laeuft."""
    global _debug_log
    _debug_log = context["debug_log"]
    _debug_log("[example_plugin] setup() ausgefuehrt")


def loop():
    """Wird alle 'loop_interval_ms' (siehe manifest.json) aufgerufen. Eine
    Exception hier deaktiviert das Plugin sofort (enabled=False,
    has_error=True) - main.py selbst laeuft unbeeintraechtigt weiter."""
    global _heartbeat_count
    _heartbeat_count += 1
    if _debug_log is not None:
        _debug_log("[example_plugin] Heartbeat #{}".format(_heartbeat_count))


def teardown():
    """Wird beim Deaktivieren/Loeschen des Plugins aufgerufen (best-effort,
    Exceptions werden vom Plugin-Manager ignoriert)."""
    if _debug_log is not None:
        _debug_log("[example_plugin] teardown() ausgefuehrt")


def render_system_slot():
    """Gibt ein HTML-Fragment zurueck, das in admin_system.html an der
    Stelle von <!--PLUGIN_SLOT:system--> eingefuegt wird. Muss ein
    eigenstaendiges HTML-Fragment sein (kein gemeinsames JS/CSS vorhanden)."""
    return (
        '<div class="plugin-setting" style="margin-top:10px;padding:8px;'
        'border:1px solid #2a3a4a;border-radius:6px">'
        '<b>example_plugin</b> - Heartbeats seit Aktivierung: {}'
        '</div>'
    ).format(_heartbeat_count)
