package com.fpv.gamification.app.ui.dashboard

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

/**
 * Natives Pendant zu source/admin_dashboard.html - reines Tab-Inhalts-
 * Composable, spricht "/data", "/system-info" und die sechs "*-log"-
 * Endpunkte direkt an.
 */
@Composable
fun DashboardScreen(viewModel: DashboardViewModel = viewModel()) {
    val score by viewModel.score.collectAsState()
    val highscore by viewModel.highscore.collectAsState()
    val activeProfile by viewModel.activeProfile.collectAsState()
    val memFreeKb by viewModel.memFreeKb.collectAsState()
    val statTiles by viewModel.statTiles.collectAsState()
    val activity by viewModel.activity.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()

    LaunchedEffect(Unit) { viewModel.refresh() }

    if (score == 0 && highscore == 0 && isLoading && statTiles.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
        }
        return
    }
    if (errorMessage != null && statTiles.isEmpty()) {
        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(errorMessage ?: "", color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
            Spacer(Modifier.height(16.dp))
            OutlinedButton(onClick = viewModel::refresh) { Text("Erneut versuchen") }
        }
        return
    }

    var showResetConfirm by remember { mutableStateOf(false) }

    LazyColumn(modifier = Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text("Status", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(6.dp))
                    StatusRow("Score", score.toString())
                    StatusRow("Highscore", highscore.toString())
                    StatusRow("Aktives Profil", activeProfile)
                    StatusRow("Freier Speicher", memFreeKb?.let { "%.1f KB".format(it) } ?: "?")
                    Spacer(Modifier.height(10.dp))
                    OutlinedButton(onClick = { showResetConfirm = true }) { Text("Highscore loeschen") }
                }
            }
        }

        item {
            Text(
                "📊 Statistik",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        // Manuelles 2-Spalten-Raster statt LazyVerticalGrid: statTiles ist immer
        // klein/fest (6 Kacheln), ein zweiter verschachtelter Lazy-Container
        // innerhalb der aeusseren LazyColumn wuerde eine explizite (und hier
        // unnoetig fehleranfaellige) Hoehenberechnung erfordern.
        items(statTiles.chunked(2)) { rowTiles ->
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                rowTiles.forEach { tile ->
                    StatTileCard(tile, modifier = Modifier.weight(1f))
                }
                if (rowTiles.size == 1) {
                    Spacer(Modifier.weight(1f))
                }
            }
        }

        item {
            Text(
                "🕐 Letzte Aktivitaet",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        if (activity.isEmpty()) {
            item {
                Text(
                    "Noch keine Aktivitaet.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            }
        } else {
            items(activity.size) { index ->
                ActivityRow(activity[index])
            }
        }
    }

    if (showResetConfirm) {
        AlertDialog(
            onDismissRequest = { showResetConfirm = false },
            title = { Text("Highscore loeschen?") },
            confirmButton = {
                TextButton(onClick = { showResetConfirm = false; viewModel.resetHighscore() }) { Text("Loeschen") }
            },
            dismissButton = { TextButton(onClick = { showResetConfirm = false }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun StatusRow(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        Text(value, color = MaterialTheme.colorScheme.onSurface, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun StatTileCard(tile: StatTile, modifier: Modifier = Modifier) {
    val accent = runCatching { Color(android.graphics.Color.parseColor(tile.colorHex)) }.getOrDefault(MaterialTheme.colorScheme.primary)
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(tile.label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(4.dp))
            Text(tile.value, style = MaterialTheme.typography.titleMedium, color = accent)
            if (tile.sub.isNotEmpty()) {
                Text(tile.sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun ActivityRow(item: ActivityItem) {
    val accent = runCatching { Color(android.graphics.Color.parseColor(item.colorHex)) }.getOrDefault(MaterialTheme.colorScheme.primary)
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(modifier = Modifier.width(8.dp).height(8.dp).background(accent, CircleShape))
        Spacer(Modifier.width(8.dp))
        Text(item.text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Text(item.time, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
