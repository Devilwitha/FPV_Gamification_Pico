package com.fpv.gamification.app.ui.plugins

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.fpv.gamification.app.data.api.PluginStatus
import com.fpv.gamification.app.data.api.StorePlugin

enum class PluginsScreenMode { INSTALLED, STORE }

private val ErrorColor = Color(0xFFC0392B)

/**
 * Eine Liste, parametrisiert per [mode]: INSTALLED zeigt die auf dem Pico
 * installierten Plugins (Aktivieren/Deaktivieren/Löschen, rote
 * Fehlermarkierung bei has_error - siehe source/pico_web_api.py's
 * "/admin-plugins" Weboberfläche fürs Pendant), STORE zeigt die im Webshop
 * verfügbaren Mods mit Download-Button.
 */
@Composable
fun PluginsScreen(
    mode: PluginsScreenMode,
    installedPlugins: List<PluginStatus> = emptyList(),
    storePlugins: List<StorePlugin> = emptyList(),
    onToggle: (String, Boolean) -> Unit = { _, _ -> },
    onDelete: (String) -> Unit = {},
    onDownload: (String) -> Unit = {},
) {
    LazyColumn(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        when (mode) {
            PluginsScreenMode.INSTALLED -> {
                if (installedPlugins.isEmpty()) {
                    item { Text("Keine Plugins installiert.") }
                }
                items(installedPlugins) { plugin ->
                    InstalledPluginCard(plugin = plugin, onToggle = onToggle, onDelete = onDelete)
                }
            }

            PluginsScreenMode.STORE -> {
                if (storePlugins.isEmpty()) {
                    item { Text("Keine Plugins im Store gefunden.") }
                }
                items(storePlugins) { plugin ->
                    StorePluginCard(plugin = plugin, onDownload = onDownload)
                }
            }
        }
    }
}

@Composable
private fun InstalledPluginCard(
    plugin: PluginStatus,
    onToggle: (String, Boolean) -> Unit,
    onDelete: (String) -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = plugin.name,
                        style = MaterialTheme.typography.titleMedium,
                        color = if (plugin.hasError) ErrorColor else Color.Unspecified,
                    )
                    Text(
                        text = "v${plugin.version}" + if (plugin.author.isNotBlank()) " · Code by ${plugin.author}" else "",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Switch(checked = plugin.enabled, onCheckedChange = { onToggle(plugin.name, it) })
            }
            if (plugin.hasError) {
                Text(
                    text = "CRASHED / FEHLER: ${plugin.errorMessage}",
                    color = ErrorColor,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(onClick = { onDelete(plugin.name) }) {
                Text("Löschen")
            }
        }
    }
}

@Composable
private fun StorePluginCard(plugin: StorePlugin, onDownload: (String) -> Unit) {
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(12.dp).fillMaxWidth(),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(text = plugin.name, style = MaterialTheme.typography.titleMedium)
                Text(
                    text = "v${plugin.version}" + if (plugin.author.isNotBlank()) " · Code by ${plugin.author}" else "",
                    style = MaterialTheme.typography.bodySmall,
                )
                if (plugin.description.isNotBlank()) {
                    Text(text = plugin.description, style = MaterialTheme.typography.bodySmall)
                }
            }
            Button(onClick = { onDownload(plugin.name) }) {
                Text("Download")
            }
        }
    }
}
