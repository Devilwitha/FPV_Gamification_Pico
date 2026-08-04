package com.fpv.gamification.app.ui.plugins

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.fpv.gamification.app.data.api.PluginStatus
import com.fpv.gamification.app.data.api.StorePlugin

enum class PluginsScreenMode { INSTALLED, STORE }

/**
 * Eine Liste, parametrisiert per [mode]: INSTALLED zeigt die auf dem Pico
 * installierten Plugins (Aktivieren/Deaktivieren/Löschen, rote
 * Fehlermarkierung bei has_error - siehe source/pico_web_api.py's
 * "/admin-plugins" Weboberfläche fürs Pendant), STORE zeigt die im Webshop
 * verfügbaren Mods mit Download-Button.
 *
 * Zeigt bei leerer Liste NIE einen unkommentierten leeren Bildschirm: je
 * nach [isLoading]/[errorMessage] entweder einen Ladeindikator oder einen
 * klar sichtbaren, klickbaren Hinweis mit "Erneut versuchen"-Button - auch
 * ganz ohne Netzwerkverbindung bleibt der Screen so immer bedienbar.
 */
@Composable
fun PluginsScreen(
    mode: PluginsScreenMode,
    installedPlugins: List<PluginStatus> = emptyList(),
    storePlugins: List<StorePlugin> = emptyList(),
    isLoading: Boolean = false,
    errorMessage: String? = null,
    onRetry: () -> Unit = {},
    onToggle: (String, Boolean) -> Unit = { _, _ -> },
    onDelete: (String) -> Unit = {},
    onDownload: (String) -> Unit = {},
) {
    val isEmpty = when (mode) {
        PluginsScreenMode.INSTALLED -> installedPlugins.isEmpty()
        PluginsScreenMode.STORE -> storePlugins.isEmpty()
    }

    if (isEmpty) {
        if (isLoading) {
            LoadingState()
        } else {
            EmptyState(
                mode = mode,
                errorMessage = errorMessage,
                onRetry = onRetry,
            )
        }
        return
    }

    LazyColumn(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        when (mode) {
            PluginsScreenMode.INSTALLED -> {
                items(installedPlugins) { plugin ->
                    InstalledPluginCard(plugin = plugin, onToggle = onToggle, onDelete = onDelete)
                }
            }

            PluginsScreenMode.STORE -> {
                items(storePlugins) { plugin ->
                    StorePluginCard(plugin = plugin, onDownload = onDownload)
                }
            }
        }
    }
}

@Composable
private fun LoadingState() {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(16.dp))
        Text("Lade...", color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EmptyState(
    mode: PluginsScreenMode,
    errorMessage: String?,
    onRetry: () -> Unit,
) {
    val isError = errorMessage != null
    val icon = if (isError) Icons.Filled.CloudOff else Icons.Filled.Extension
    val title = if (isError) {
        "Keine Verbindung"
    } else if (mode == PluginsScreenMode.INSTALLED) {
        "Keine Plugins installiert"
    } else {
        "Keine Plugins im Store gefunden"
    }
    val message = errorMessage ?: if (mode == PluginsScreenMode.INSTALLED) {
        "Der Pico hat aktuell keine Mods gemeldet."
    } else {
        "Der Webshop-Store liefert momentan keine Mods."
    }
    val tint = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant

    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(56.dp))
        Spacer(Modifier.height(16.dp))
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(20.dp))
        Button(onClick = onRetry) {
            Icon(Icons.Filled.Refresh, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Erneut versuchen")
        }
    }
}

@Composable
private fun InstalledPluginCard(
    plugin: PluginStatus,
    onToggle: (String, Boolean) -> Unit,
    onDelete: (String) -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 3.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = plugin.name,
                        style = MaterialTheme.typography.titleMedium,
                        color = if (plugin.hasError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        text = "v${plugin.version}" + if (plugin.author.isNotBlank()) " · Code by ${plugin.author}" else "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(
                    checked = plugin.enabled,
                    onCheckedChange = { onToggle(plugin.name, it) },
                    colors = SwitchDefaults.colors(checkedTrackColor = MaterialTheme.colorScheme.primary),
                )
            }
            if (plugin.hasError) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                    Icon(
                        Icons.Filled.Error,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.error,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = "CRASHED / FEHLER: ${plugin.errorMessage}",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            TextButton(onClick = { onDelete(plugin.name) }) {
                Icon(Icons.Filled.Delete, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("Löschen")
            }
        }
    }
}

@Composable
private fun StorePluginCard(plugin: StorePlugin, onDownload: (String) -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 3.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(14.dp).fillMaxWidth(),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = plugin.name,
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = "v${plugin.version}" + if (plugin.author.isNotBlank()) " · Code by ${plugin.author}" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (plugin.description.isNotBlank()) {
                    Text(
                        text = plugin.description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            Button(onClick = { onDownload(plugin.name) }) {
                Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("Download")
            }
        }
    }
}
