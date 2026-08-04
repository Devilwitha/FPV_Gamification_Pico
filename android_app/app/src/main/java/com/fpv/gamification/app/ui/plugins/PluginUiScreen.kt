package com.fpv.gamification.app.ui.plugins

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.fpv.gamification.app.data.api.PluginUiButton
import com.fpv.gamification.app.data.api.PluginUiField
import com.fpv.gamification.app.data.api.PluginUiSection
import com.fpv.gamification.app.ui.theme.FpvColors
import com.google.gson.JsonElement
import com.google.gson.JsonObject

@Composable
private fun rememberPluginUiViewModel(pluginName: String): PluginUiViewModel {
    return viewModel(
        key = "plugin-ui-$pluginName",
        factory = viewModelFactory { initializer { PluginUiViewModel(pluginName) } },
    )
}

/**
 * Generischer, schema-getriebener Plugin-Screen (siehe PluginUiViewModel +
 * data/api/PluginUiModels.kt): rendert komplett nativ, OHNE dass fuer ein
 * einzelnes Plugin (z.B. "shooter") eigener UI-Code in dieser Datei stehen
 * muesste - jedes Plugin, das ein manifest.json "ui_pages.main" deklariert,
 * wird hierueber dargestellt. "Uebernimmt" dabei den ganzen Bildschirm
 * (eigenes Scaffold/TopAppBar), analog zur Browser-Seite des Plugins.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PluginUiScreen(pluginName: String, onBack: () -> Unit) {
    val viewModel = rememberPluginUiViewModel(pluginName)
    val schema by viewModel.schema.collectAsState()
    val data by viewModel.data.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()
    val statusMessage by viewModel.statusMessage.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(pluginName) { viewModel.load() }
    LaunchedEffect(statusMessage) {
        statusMessage?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearStatusMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(schema?.title?.ifBlank { null } ?: pluginName.replaceFirstChar(Char::uppercase)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Zurück")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                    navigationIconContentColor = MaterialTheme.colorScheme.onSurface,
                ),
            )
        },
    ) { padding ->
        val currentSchema = schema
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            when {
                currentSchema == null && isLoading -> {
                    CircularProgressIndicator(
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.align(Alignment.Center),
                    )
                }
                currentSchema == null -> {
                    Column(
                        modifier = Modifier.fillMaxSize().padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text(errorMessage ?: "Kein UI-Schema verfuegbar.", color = MaterialTheme.colorScheme.error)
                        Spacer(Modifier.height(16.dp))
                        OutlinedButton(onClick = viewModel::load) { Text("Erneut versuchen") }
                    }
                }
                else -> {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize().padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(currentSchema.sections.size) { index ->
                            SectionView(currentSchema.sections[index], data, onAction = viewModel::sendAction)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionView(section: PluginUiSection, data: JsonObject?, onAction: (String, Map<String, String>) -> Unit) {
    when (section.type) {
        "stats" -> StatsSection(section, data)
        "form" -> FormSection(section, data, onAction)
        "actions" -> ActionsSection(section, onAction)
        "list" -> ListSection(section, data)
    }
}

@Composable
private fun StatsSection(section: PluginUiSection, data: JsonObject?) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            section.title?.let {
                Text(it, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
            }
            section.fields.forEach { field ->
                val display = computeFieldDisplay(field, data)
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(field.label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(display.text, style = MaterialTheme.typography.bodySmall, color = display.tone.toColor())
                }
            }
        }
    }
}

@Composable
private fun FormSection(section: PluginUiSection, data: JsonObject?, onAction: (String, Map<String, String>) -> Unit) {
    val fieldStates = remember(section) { mutableStateMapOf<String, String>() }
    var initialized by remember(section) { mutableStateOf(false) }

    LaunchedEffect(data) {
        if (!initialized && data != null) {
            val config = data.getPath("config")?.takeIf { it.isJsonObject }?.asJsonObject
            section.fields.forEach { field ->
                val raw = config?.get(field.key)
                fieldStates[field.key] = if (field.kind == "toggle") (raw.asBooleanOrNull() ?: false).toString() else (raw.asStringOrNull() ?: "")
            }
            initialized = true
        }
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            section.title?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
            section.hint?.let {
                Spacer(Modifier.height(4.dp))
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(8.dp))
            section.fields.forEach { field ->
                when (field.kind) {
                    "toggle" -> {
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(field.label, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                            Switch(
                                checked = fieldStates[field.key] == "true",
                                onCheckedChange = { fieldStates[field.key] = it.toString() },
                            )
                        }
                    }
                    "number" -> {
                        OutlinedTextField(
                            value = fieldStates[field.key] ?: "",
                            onValueChange = { fieldStates[field.key] = it.filter { c -> c.isDigit() } },
                            label = { Text(field.label) },
                            singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        )
                    }
                    else -> {}
                }
            }
            Spacer(Modifier.height(8.dp))
            Button(onClick = {
                val endpoint = section.submitEndpoint ?: return@Button
                val payload = section.fields.associate { field ->
                    field.key to when (field.kind) {
                        "toggle" -> if (fieldStates[field.key] == "true") "1" else "0"
                        else -> fieldStates[field.key] ?: ""
                    }
                }
                onAction(endpoint, payload)
            }) { Text(section.submitLabel ?: "Speichern") }
        }
    }
}

@Composable
private fun ActionsSection(section: PluginUiSection, onAction: (String, Map<String, String>) -> Unit) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        section.buttons.forEach { button -> ActionButton(button, onAction) }
    }
}

@Composable
private fun ActionButton(button: PluginUiButton, onAction: (String, Map<String, String>) -> Unit) {
    when (button.style) {
        "accent" -> Button(
            onClick = { onAction(button.endpoint, emptyMap()) },
            colors = ButtonDefaults.buttonColors(containerColor = FpvColors.Accent, contentColor = FpvColors.OnPrimary),
        ) { Text(button.label) }
        "muted" -> OutlinedButton(onClick = { onAction(button.endpoint, emptyMap()) }) { Text(button.label) }
        else -> Button(onClick = { onAction(button.endpoint, emptyMap()) }) { Text(button.label) }
    }
}

@Composable
private fun ListSection(section: PluginUiSection, data: JsonObject?) {
    val entries = section.sourceKey?.let { data?.getPath(it) }?.takeIf { it.isJsonArray }?.asJsonArray
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            section.title?.let {
                Text(it, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
            }
            if (entries == null || entries.size() == 0) {
                Text(
                    section.emptyText ?: "Keine Eintraege.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                entries.forEach { element ->
                    val obj = element.takeIf { it.isJsonObject }?.asJsonObject
                    val label = (section.itemLabelPrefix ?: "") + (obj?.get(section.itemLabelKey)?.asStringOrNull() ?: "-")
                    val value = obj?.get(section.itemValueKey)?.asStringOrNull() ?: "-"
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(value, style = MaterialTheme.typography.bodySmall, color = FpvColors.Accent)
                    }
                }
            }
        }
    }
}

// ==================== Dynamische Feld-Auswertung (Poll-JSON) ====================

private enum class FieldTone { NORMAL, POSITIVE, NEGATIVE }

private data class FieldDisplay(val text: String, val tone: FieldTone = FieldTone.NORMAL)

@Composable
private fun FieldTone.toColor(): Color = when (this) {
    FieldTone.POSITIVE -> FpvColors.Primary
    FieldTone.NEGATIVE -> FpvColors.Error
    FieldTone.NORMAL -> MaterialTheme.colorScheme.onSurface
}

private fun JsonObject.getPath(path: String): JsonElement? {
    var current: JsonElement? = this
    for (part in path.split(".")) {
        val obj = current?.takeIf { it.isJsonObject }?.asJsonObject ?: return null
        current = obj.get(part) ?: return null
    }
    return current
}

private fun JsonElement?.asBooleanOrNull(): Boolean? =
    this?.takeIf { !it.isJsonNull && it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean

private fun JsonElement?.asStringOrNull(): String? =
    this?.takeIf { !it.isJsonNull }?.let { if (it.isJsonPrimitive) it.asString else it.toString() }

private fun JsonElement?.asIntOrNull(): Int? =
    this?.takeIf { !it.isJsonNull && it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asInt

private fun computeFieldDisplay(field: PluginUiField, data: JsonObject?): FieldDisplay {
    val value = data?.getPath(field.key)
    return when (field.kind) {
        "bool_text" -> {
            val flag = value.asBooleanOrNull() ?: false
            FieldDisplay(if (flag) (field.trueText ?: "Ja") else (field.falseText ?: "Nein"))
        }
        "node_ref" -> {
            val ref = value?.asStringOrNull()
            if (ref == null) FieldDisplay("-") else FieldDisplay("Node $ref")
        }
        "bool_dot" -> {
            val flag = value.asBooleanOrNull() ?: false
            FieldDisplay(if (flag) "Verfügbar" else "Nicht verfügbar", if (flag) FieldTone.POSITIVE else FieldTone.NEGATIVE)
        }
        "lives_remaining" -> {
            val remaining = value.asIntOrNull() ?: 0
            val configLives = data?.getPath("config.lives").asIntOrNull()
            if (remaining == 0 && configLives == 0) FieldDisplay("Unbegrenzt") else FieldDisplay(remaining.toString())
        }
        "aux_dot" -> {
            val aux = value?.takeIf { it.isJsonObject }?.asJsonObject
            val channel = aux?.get("channel").asIntOrNull() ?: 0
            val available = aux?.get("available").asBooleanOrNull() ?: false
            when {
                channel <= 0 -> FieldDisplay("Deaktiviert")
                !available -> FieldDisplay("Kein Signal", FieldTone.NEGATIVE)
                else -> {
                    val valueUs = aux?.get("value_us").asIntOrNull() ?: 0
                    val threshold = aux?.get("threshold_us").asIntOrNull() ?: 0
                    if (valueUs >= threshold) FieldDisplay("Ausgelöst", FieldTone.POSITIVE) else FieldDisplay("Unter Schwelle")
                }
            }
        }
        "aux_value" -> {
            val aux = value?.takeIf { it.isJsonObject }?.asJsonObject
            val channel = aux?.get("channel").asIntOrNull() ?: 0
            val available = aux?.get("available").asBooleanOrNull() ?: false
            if (channel <= 0 || !available) {
                FieldDisplay("-")
            } else {
                val valueUs = aux?.get("value_us").asIntOrNull() ?: 0
                val threshold = aux?.get("threshold_us").asIntOrNull() ?: 0
                FieldDisplay("$valueUs us (Kanal $channel, Schwelle $threshold us)")
            }
        }
        else -> FieldDisplay(value?.asStringOrNull() ?: "-")
    }
}
