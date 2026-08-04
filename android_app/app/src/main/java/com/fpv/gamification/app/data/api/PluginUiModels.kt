package com.fpv.gamification.app.data.api

import com.google.gson.annotations.SerializedName

/**
 * Native UI-Beschreibung eines Plugins (siehe source/plugin_manager.py's
 * get_ui_schema() und z.B. source/mods/shooter/main.py's get_ui_schema()) -
 * bewusst als generisches Schema statt fest verdrahteter Kotlin-Klasse pro
 * Plugin: PluginUiScreen.kt rendert JEDES Schema, das diese Form hat, ohne
 * dass fuer ein neues Plugin eigener App-Code noetig waere. Die eigentlichen
 * Live-Daten/Aktionen kommen weiterhin ueber die vom Plugin selbst
 * deklarierten Endpunkte (poll_endpoint/submit_endpoint/button.endpoint),
 * NICHT ueber dieses Schema selbst.
 */
data class PluginUiSchemaResponse(
    val ok: Boolean = false,
    val schema: PluginUiSchema? = null,
    val error: String? = null,
)

data class PluginUiSchema(
    val title: String = "",
    @SerializedName("poll_endpoint") val pollEndpoint: String? = null,
    @SerializedName("poll_interval_ms") val pollIntervalMs: Long = 1000,
    val sections: List<PluginUiSection> = emptyList(),
)

/**
 * Ein Abschnitt der Seite - [type] bestimmt, welche der uebrigen Felder
 * relevant sind (siehe PluginUiScreen.kt's SectionView()):
 * - "stats": nur [fields] (Anzeige, kein Eingabefeld)
 * - "form": [fields] (Eingabefelder) + [submitEndpoint]/[submitLabel]/[hint]
 * - "actions": nur [buttons]
 * - "list": [sourceKey]/[itemLabelKey]/[itemLabelPrefix]/[itemValueKey]/[emptyText]
 */
data class PluginUiSection(
    val type: String = "",
    val title: String? = null,
    val fields: List<PluginUiField> = emptyList(),
    @SerializedName("submit_endpoint") val submitEndpoint: String? = null,
    @SerializedName("submit_label") val submitLabel: String? = null,
    val hint: String? = null,
    val buttons: List<PluginUiButton> = emptyList(),
    @SerializedName("source_key") val sourceKey: String? = null,
    @SerializedName("item_label_key") val itemLabelKey: String? = null,
    @SerializedName("item_label_prefix") val itemLabelPrefix: String? = null,
    @SerializedName("item_value_key") val itemValueKey: String? = null,
    @SerializedName("empty_text") val emptyText: String? = null,
)

/**
 * [kind] waehlt die Darstellung/das Eingabe-Widget (siehe
 * PluginUiScreen.kt's StatRow()/FormFieldInput()):
 * "text" | "bool_text" | "node_ref" | "bool_dot" | "lives_remaining" |
 * "aux_dot" | "aux_value" (alle read-only, fuer "stats"-Abschnitte) sowie
 * "toggle" | "number" (editierbar, fuer "form"-Abschnitte). [key] ist
 * sowohl der Pfad im Poll-JSON (Punkt-Notation fuer verschachtelte Objekte,
 * z.B. "hardware.emitter_available") als auch - bei "form"-Feldern - der
 * Formularfeld-Name beim Absenden an submit_endpoint.
 */
data class PluginUiField(
    val key: String = "",
    val label: String = "",
    val kind: String = "text",
    @SerializedName("true_text") val trueText: String? = null,
    @SerializedName("false_text") val falseText: String? = null,
    val min: Double? = null,
    val max: Double? = null,
    val step: Double? = null,
)

data class PluginUiButton(
    val label: String = "",
    val endpoint: String = "",
    val style: String = "primary",
)
