"""my_plugin - Vorlage fuer ein eigenes Plugin.

Kopiere diesen ganzen Ordner nach source/mods/<dein_name>/, passe
manifest.json's "name" an den Ordnernamen an und implementiere die
gewuenschten Funktionen unten. Alles ist optional ausser der Datei selbst -
plugin_manager.py ruft jede Funktion nur auf, wenn sie tatsaechlich
existiert (siehe getattr()-Aufrufe dort), ein fehlendes handle_route()/
render_system_slot() ist also kein Fehler.

Siehe template/README.md fuer die vollstaendige Erklaerung aller Hooks,
das Manifest-Schema und wie ein fertiges Plugin deployt/hochgeladen wird.
Zwei durchgerechnete Referenz-Beispiele im Repo:
- source/mods/example_plugin/ - minimal (nur Heartbeat + System-Tab-Slot).
- source/mods/shooter/ - vollstaendig (eigene Spiellogik, eigene Hardware-
  Treiber als Untermodule, eigene Weboberflaeche via handle_route()).
"""

_context = None


def setup(context):
    """Wird EINMALIG aufgerufen, wenn das Plugin aktiviert wird (beim Boot,
    oder wenn es ueber die Weboberflaeche/API wieder eingeschaltet wird).
    context ist ein dict mit:
      - context["debug_log"](message): loggt wie main.py's eigenes Logging.
      - context["plugin_dir"]: Pfad dieses Plugin-Ordners (z.B. "mods/my_plugin"),
        falls du eigene Dateien (Config, Log) im selben Ordner ablegen willst.

    Wirft diese Funktion eine Exception, wird das Plugin SOFORT als
    enabled=False/has_error=True markiert (samt Fehlermeldung) und niemals
    aktiviert - main.py selbst bleibt davon unberuehrt."""
    global _context
    _context = context
    context["debug_log"]("[my_plugin] setup() ausgefuehrt")


def loop():
    """Wird alle 'loop_interval_ms' (siehe manifest.json) aufgerufen,
    solange das Plugin aktiv ist. Synchron, KEIN async/await - fuer echte
    Wartezeiten/Polling-Intervalle ist genau das der Sinn von
    loop_interval_ms, nicht ein eigenes time.sleep() hier drin (wuerde die
    gesamte Firmware blockieren).

    Wirft loop() eine Exception, wird das Plugin SOFORT deaktiviert
    (enabled=False, has_error=True, error_message gesetzt) und aus der
    aktiven Schleife entfernt - kein main.py-Crash, aber auch kein
    automatischer Neustart-Versuch (der Nutzer muss es ueber die
    Weboberflaeche/API manuell wieder aktivieren, siehe unten)."""
    pass


def teardown():
    """Wird aufgerufen, wenn das Plugin deaktiviert oder geloescht wird -
    fuer Aufraeumarbeiten (z.B. eine laufende Aktion sauber stoppen).
    Exceptions hier werden vom Plugin-Manager ignoriert (best effort)."""
    pass


def render_system_slot():
    """Optional: nur noetig, wenn manifest.json's "ui_slots" diesen
    Funktionsnamen fuer den Slot "system" nennt (siehe Vorlage oben). Gibt
    ein HTML-Fragment zurueck, das in den bestehenden "System"-Tab der Pico-
    Weboberflaeche eingeblendet wird (admin_system.html's
    <!--PLUGIN_SLOT:system--> Marker). Analog gibt es den Slot "idcard" fuer
    den "Ausweis"-Tab (admin_idcard.html). Das Fragment ist ein
    eigenstaendiges HTML-Snippet - es gibt keine gemeinsame CSS/JS-Basis mit
    dem Rest der Seite, bei Bedarf ein eigenes <style>/<script> einbetten.

    Baut dein Plugin einen eigenen Spielmodus mit eigener Admin-Seite (siehe
    handle_route() unten), soll er meist auch im Dashboard (/admin) und in
    der Zuschauer-Ansicht (/gamemodes-view) auftauchen - dafuer gibt es die
    Slots "dashboard_nav"/"dashboard_card"/"dashboard_stat"/
    "dashboard_script"/"gamemodes_button" weiter unten (auskommentiert, da
    sie nur zusammen mit einer eigenen Admin-Seite Sinn ergeben)."""
    return '<div class="plugin-setting">my_plugin ist aktiv</div>'


# ==================== Eigener Spielmodus mit eigener Admin-Seite (optional) ====================
# Alles ab hier ist NUR noetig, wenn dein Plugin (wie source/mods/shooter/)
# einen eigenstaendigen Spielmodus mit eigener Admin-Unterseite baut, statt
# nur ein Info-Fragment wie render_system_slot() oben zu liefern. Zum
# Aktivieren: Funktionen unten einkommentieren UND die jeweiligen Eintraege
# in manifest.json ergaenzen (siehe Kommentare je Funktion).

# async def handle_route(writer, request_path, request_method, query_params, body_params):
#     """Optional: nur noetig, wenn dein Plugin EIGENE HTTP-Routen bedienen
#     soll (z.B. eine eigene Admin-Unterseite wie /admin-my_plugin, oder eine
#     JSON-API wie /my_plugin-data). Dazu manifest.json's "route_prefixes"
#     mit den betroffenen Pfaden fuellen, z.B.:
#         "route_prefixes": ["/admin-my_plugin", "/my_plugin-"]
#     plugin_manager.handle_plugin_route() ruft diese Funktion fuer jeden
#     Request auf, dessen Pfad zu einem der Praefixe passt (Gleichheit oder
#     startswith). Gib True zurueck, wenn du den Request bedient hast (dann
#     wird nicht weitergesucht), sonst False. Ein Absturz hier deaktiviert
#     das Plugin sofort, exakt wie bei loop().
#
#     Siehe source/mods/shooter/main.py fuer ein vollstaendiges Beispiel:
#     dort bedient handle_route() sowohl eine eigene Admin-HTML-Seite
#     (per main.send_html_file(), aus dem eigenen Plugin-Ordner) als auch
#     eine eigene JSON-API fuer die Spiellogik.
#     """
#     if request_path == "/admin-my_plugin":
#         return False  # eigene Seite ausliefern, siehe shooter-Beispiel
#     return False


# def render_dashboard_nav_slot():
#     """Slot "dashboard_nav" - Nav-Link im Dashboard (/admin) und auf JEDER
#     anderen Admin-Unterseite (admin_update.html, admin_system.html, ...),
#     die alle denselben <!--PLUGIN_SLOT:dashboard_nav--> Marker teilen.
#     manifest.json's "ui_slots" braucht dafuer:
#         "dashboard_nav": "render_dashboard_nav_slot"
#     Erscheint NUR, solange dieses Plugin aktiv ist (get_ui_slot_html()
#     iteriert ausschliesslich ueber aktive Plugins) - kein Sonderfall noetig,
#     um den Link beim Deaktivieren wieder auszublenden."""
#     return '<a href="/admin-my_plugin">My Plugin</a>'


# def render_dashboard_card_slot():
#     """Slot "dashboard_card" - Kachel im Dashboard-Grid (admin_dashboard.html's
#     <!--PLUGIN_SLOT:dashboard_card--> Marker). manifest.json: "dashboard_card":
#     "render_dashboard_card_slot"."""
#     return (
#         '<a class="card" href="/admin-my_plugin">'
#         '<h3>My Plugin</h3><p>Kurzbeschreibung, was dieser Modus macht.</p>'
#         '</a>'
#     )


# def render_dashboard_stat_slot():
#     """Slot "dashboard_stat" - Statistikkachel im Dashboard (<!--PLUGIN_SLOT:
#     dashboard_stat--> Marker). Eigene Element-IDs verwenden (hier
#     st_my_plugin_val/-sub) - render_dashboard_script_slot() unten befuellt
#     sie per JS. manifest.json: "dashboard_stat": "render_dashboard_stat_slot"."""
#     return (
#         '<div class="stile" style="--sc:#2980b9"><div class="sticon">&#127919;</div>'
#         '<div class="stbody"><div class="stlabel">My Plugin</div>'
#         '<div class="stval" id="st_my_plugin_val">-</div><div class="stsub" id="st_my_plugin_sub"></div></div></div>'
#     )


# def render_dashboard_script_slot():
#     """Slot "dashboard_script" - eigenes <script>-Fragment (<!--PLUGIN_SLOT:
#     dashboard_script--> Marker), das sich selbst in
#     window.DASHBOARD_HOOKS eintraegt: jeder Hook wird bei jedem
#     loadStats()-Zyklus des Dashboards aufgerufen, aktualisiert seine eigene
#     Statistikkachel per document.getElementById() UND liefert (als Promise)
#     eine Liste von Activity-Feed-Eintraegen ({ts, time, color, text})
#     zurueck - das Dashboard-Kernskript kennt "my_plugin" dabei an keiner
#     Stelle namentlich. manifest.json: "dashboard_script":
#     "render_dashboard_script_slot". Vollstaendiges Beispiel (inkl. eigenem
#     JSON-Log-Endpunkt) siehe source/mods/shooter/main.py."""
#     return """<script>
# (function(){
# window.DASHBOARD_HOOKS=window.DASHBOARD_HOOKS||[];
# window.DASHBOARD_HOOKS.push(function(){
# var valEl=document.getElementById('st_my_plugin_val');
# if(valEl)valEl.innerText='aktiv';
# return [];
# });
# })();
# </script>"""


# def render_gamemodes_button_slot():
#     """Slot "gamemodes_button" - Steuer-Button auf der oeffentlichen
#     Zuschauer-Ansicht gamemodes_view.html (<!--PLUGIN_SLOT:gamemodes_button-->
#     Marker). manifest.json: "gamemodes_button": "render_gamemodes_button_slot"."""
#     return '<a class="b" href="/admin-my_plugin">My Plugin steuern</a>'
