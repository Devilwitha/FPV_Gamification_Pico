package com.fpv.gamification.app.ui.system

import android.content.ClipData
import android.content.ClipboardManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.fpv.gamification.app.data.api.SystemInfo
import com.fpv.gamification.app.ui.theme.FpvColors
import kotlin.math.roundToInt

/**
 * Natives Pendant zu source/admin_system.html - reines Tab-Inhalts-
 * Composable (kein eigenes Scaffold/TopAppBar, siehe [com.fpv.gamification.app.ui.main.MainScreen]),
 * spricht die bereits bestehenden Pico-JSON-Endpunkte direkt an.
 */
@Composable
fun SystemScreen(
    viewModel: SystemViewModel = viewModel(),
    onOpenPluginUi: (String) -> Unit = {},
) {
    val systemInfo by viewModel.systemInfo.collectAsState()
    val hotspotConfig by viewModel.hotspotConfig.collectAsState()
    val wlanConfig by viewModel.wlanConfig.collectAsState()
    val languages by viewModel.languages.collectAsState()
    val pluginsWithUi by viewModel.pluginsWithUi.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()
    val context = LocalContext.current

    LaunchedEffect(Unit) { viewModel.refresh() }

    if (systemInfo == null && isLoading) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
        }
        return
    }
    if (systemInfo == null && errorMessage != null) {
        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(errorMessage ?: "", color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(16.dp))
            Button(onClick = viewModel::refresh) { Text("Erneut versuchen") }
        }
        return
    }

    val licensePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { viewModel.uploadLicenseFile(context, it, "license.lic") }
    }
    val publicKeyPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { viewModel.uploadLicenseFile(context, it, "public_key.pem") }
    }

    LazyColumn(modifier = Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { InfoSection(systemInfo) }

        if (systemInfo?.licenseStatus != "VALID") {
            item {
                LicenseSection(
                    onPickLicense = { licensePicker.launch(arrayOf("*/*")) },
                    onPickPublicKey = { publicKeyPicker.launch(arrayOf("*/*")) },
                )
            }
        }

        if (pluginsWithUi.isNotEmpty()) {
            item { PluginsSection(pluginsWithUi, onOpenPluginUi) }
        }

        item { LanguageSection(languages, onSelect = viewModel::setLanguage) }

        item {
            NetworkFormSection(
                title = "Hotspot-Konfiguration",
                initialSsid = hotspotConfig.ssid,
                initialPassword = hotspotConfig.password,
                passwordOptional = false,
                secondaryLabel = "Speichern",
                primaryLabel = "Speichern & Neustart",
                onSecondary = { ssid, password -> viewModel.saveHotspot(ssid, password, restart = false) },
                onPrimary = { ssid, password -> viewModel.saveHotspot(ssid, password, restart = true) },
            )
        }

        item {
            NetworkFormSection(
                title = "WLAN-Verbindung (fuer Update-Suche)",
                hint = "Wird nur fuer \"Nach Updates suchen\" genutzt. Der Pico verbindet sich damit kurz, um bei GitHub nach neuer Firmware zu suchen.",
                initialSsid = wlanConfig.ssid,
                initialPassword = wlanConfig.password,
                passwordOptional = true,
                secondaryLabel = "Speichern",
                onSecondary = { ssid, password -> viewModel.saveWlan(ssid, password) },
            )
        }

        item { DeviceRoleSection(systemInfo, onReset = viewModel::resetDeviceRole) }

        item {
            DeveloperModeSection(
                enabled = systemInfo?.developerMode == true,
                onToggle = viewModel::setDeveloperMode,
            )
        }

        item {
            LogsSection(
                onClearDebug = viewModel::clearDebugLog,
                onClearSession = viewModel::clearSessionLog,
            )
        }

        item {
            RestartAndEmergencySection(
                onRestart = viewModel::restartPico,
                onDeleteMain = viewModel::emergencyDeleteMain,
                onDeleteBoot = viewModel::emergencyDeleteBoot,
            )
        }
    }
}

private fun fmtBytes(n: Long?): String {
    if (n == null || n < 0) return "?"
    return if (n >= 1024 * 1024) "%.2f MB".format(n / (1024.0 * 1024.0)) else "%.1f KB".format(n / 1024.0)
}

private fun fmtUptime(totalSeconds: Long): String {
    val h = totalSeconds / 3600
    val m = (totalSeconds % 3600) / 60
    val s = totalSeconds % 60
    return "${h}h ${m}m ${s}s"
}

@Composable
private fun StatRow(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.onSurface) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        Text(value, color = valueColor, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun InfoSection(systemInfo: SystemInfo?) {
    val info = systemInfo ?: SystemInfo()
    val context = LocalContext.current
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Info", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            StatRow("Firmware-Version", if (info.firmwareVersion.isNotBlank()) "v${info.firmwareVersion}" else "-")
            StatRow("SSID", info.ssid.ifBlank { "-" })
            StatRow("IP-Adresse", info.ip.ifBlank { "-" })
            StatRow("Freier Speicher", fmtBytes(info.memFree))
            StatRow("Belegter Speicher", fmtBytes(info.memAlloc))
            StatRow("Daten-Speicher frei", fmtBytes(info.fsFree))
            StatRow("Daten-Speicher belegt", fmtBytes(info.fsUsed))
            StatRow("Laufzeit", fmtUptime(info.uptimeS))
            StatRow("Aktives Trick-Profil", info.trickTuningProfile.ifBlank { "-" })
            StatRow("OTA aktiv", if (info.otaActive) "Ja" else "Nein")
            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Geraete-ID (UID)", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(info.hardwareId.ifBlank { "-" }, style = MaterialTheme.typography.bodySmall)
                    IconButton(onClick = {
                        if (info.hardwareId.isNotBlank()) {
                            val clipboard = context.getSystemService(ClipboardManager::class.java)
                            clipboard?.setPrimaryClip(ClipData.newPlainText("Hardware-ID", info.hardwareId))
                        }
                    }) {
                        Icon(Icons.Filled.ContentCopy, contentDescription = "Kopieren", modifier = Modifier.height(16.dp))
                    }
                }
            }
            val (licenseText, licenseColor) = when (info.licenseStatus) {
                "VALID" -> "Gueltig" to FpvColors.Primary
                "INVALID" -> "Ungueltig" to FpvColors.Error
                else -> "Keine vorhanden" to FpvColors.Accent
            }
            StatRow("Lizenz", licenseText, licenseColor)
            StatRow("main", if (info.mainPresent) "Vorhanden" else "Nicht gefunden", if (info.mainPresent) FpvColors.Primary else FpvColors.Error)
            StatRow("boot", if (info.bootPresent) "Vorhanden" else "Nicht gefunden", if (info.bootPresent) FpvColors.Primary else FpvColors.Error)
            if (info.otaActive && info.otaTotalChunks > 0) {
                Spacer(Modifier.height(6.dp))
                val pct = (info.otaReceivedChunks * 100f / info.otaTotalChunks).roundToInt()
                Text("OTA: $pct %", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun LicenseSection(onPickLicense: () -> Unit, onPickPublicKey: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Lizenz", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Text(
                "Ohne gueltige Lizenz sind die Hauptfunktionen gesperrt - nur diese System-Seite ist erreichbar.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onPickLicense) { Text("license.lic auswaehlen") }
                OutlinedButton(onClick = onPickPublicKey) { Text("public_key.pem auswaehlen") }
            }
        }
    }
}

@Composable
private fun PluginsSection(entries: List<PluginUiEntry>, onOpenPluginUi: (String) -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Plugins", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            entries.forEach { entry ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Extension, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(10.dp))
                        Text(entry.title, color = MaterialTheme.colorScheme.onSurface)
                    }
                    IconButton(onClick = { onOpenPluginUi(entry.name) }) {
                        Icon(Icons.Filled.ChevronRight, contentDescription = "Oeffnen")
                    }
                }
            }
        }
    }
}

@Composable
private fun LanguageSection(languages: com.fpv.gamification.app.data.api.LanguagePacksResponse, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Sprache", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("UI-Sprache", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                Box {
                    OutlinedButton(onClick = { expanded = true }) { Text(languages.current.uppercase()) }
                    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                        languages.languages.forEach { code ->
                            DropdownMenuItem(text = { Text(code.uppercase()) }, onClick = {
                                expanded = false
                                onSelect(code)
                            })
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NetworkFormSection(
    title: String,
    hint: String? = null,
    initialSsid: String,
    initialPassword: String,
    passwordOptional: Boolean,
    secondaryLabel: String,
    primaryLabel: String? = null,
    onSecondary: (String, String) -> Unit,
    onPrimary: ((String, String) -> Unit)? = null,
) {
    var ssid by rememberSaveable(initialSsid) { mutableStateOf(initialSsid) }
    var password by rememberSaveable(initialPassword) { mutableStateOf(initialPassword) }
    var showPassword by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            if (hint != null) {
                Spacer(Modifier.height(4.dp))
                Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = ssid,
                onValueChange = { ssid = it },
                label = { Text("SSID") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text(if (passwordOptional) "Passwort (leer = offenes WLAN)" else "Passwort") },
                singleLine = true,
                visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                Switch(checked = showPassword, onCheckedChange = { showPassword = it })
                Spacer(Modifier.width(8.dp))
                Text("Passwort anzeigen", style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { onSecondary(ssid, password) }) { Text(secondaryLabel) }
                if (primaryLabel != null && onPrimary != null) {
                    Button(onClick = { onPrimary(ssid, password) }) { Text(primaryLabel) }
                }
            }
        }
    }
}

@Composable
private fun DeviceRoleSection(systemInfo: SystemInfo?, onReset: () -> Unit) {
    var showConfirm by remember { mutableStateOf(false) }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Geraete-Rolle", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            StatRow("Aktuelle Rolle", if (systemInfo?.deviceRole == "gatehill") "Gate/Hill Pico" else "Gamification Pico")
            StatRow("Hardware", systemInfo?.boardType?.ifBlank { "-" } ?: "-")
            Spacer(Modifier.height(6.dp))
            Text(
                "Setzt nur die gespeicherte Geraete-Rolle zurueck und startet neu - danach erscheint wieder die Ersteinrichtungs-Seite.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = { showConfirm = true }) { Text("Rolle zuruecksetzen") }
        }
    }
    if (showConfirm) {
        AlertDialog(
            onDismissRequest = { showConfirm = false },
            title = { Text("Geraete-Rolle zuruecksetzen?") },
            text = { Text("Der Pico startet neu und zeigt danach wieder die Ersteinrichtungs-Seite.") },
            confirmButton = {
                TextButton(onClick = { showConfirm = false; onReset() }) { Text("Zuruecksetzen") }
            },
            dismissButton = { TextButton(onClick = { showConfirm = false }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun DeveloperModeSection(enabled: Boolean, onToggle: (Boolean) -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Developer-Modus", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Einzeldatei-Uploads (.py/.html) per OTA erlauben",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                Switch(checked = enabled, onCheckedChange = onToggle)
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "Standardmaessig (aus) akzeptiert OTA nur komplette firmware.nbo Bundles.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun LogsSection(onClearDebug: () -> Unit, onClearSession: () -> Unit) {
    var confirmTarget by remember { mutableStateOf<String?>(null) }
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Log-Dateien", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { confirmTarget = "debug" }) { Text("Debug-Log loeschen") }
                OutlinedButton(onClick = { confirmTarget = "session" }) { Text("Session-Log loeschen") }
            }
        }
    }
    val target = confirmTarget
    if (target != null) {
        AlertDialog(
            onDismissRequest = { confirmTarget = null },
            title = { Text("Wirklich loeschen?") },
            text = { Text(if (target == "debug") "Debug-Log TXT" else "Session-Log TXT") },
            confirmButton = {
                TextButton(onClick = {
                    confirmTarget = null
                    if (target == "debug") onClearDebug() else onClearSession()
                }) { Text("Loeschen") }
            },
            dismissButton = { TextButton(onClick = { confirmTarget = null }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun RestartAndEmergencySection(onRestart: () -> Unit, onDeleteMain: () -> Unit, onDeleteBoot: () -> Unit) {
    var armMain by remember { mutableStateOf(false) }
    var armBoot by remember { mutableStateOf(false) }
    var confirmMain by remember { mutableStateOf(false) }
    var confirmBoot by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text("Restart", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Button(onClick = onRestart) { Text("Restart") }

            Spacer(Modifier.height(14.dp))
            Text(
                "Nur bei Bootloop nutzen. Loescht gezielt eine Datei und startet sofort neu.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Notaus Main freigeben", style = MaterialTheme.typography.bodySmall)
                Switch(checked = armMain, onCheckedChange = { armMain = it })
            }
            OutlinedButton(onClick = { confirmMain = true }, enabled = armMain) {
                Text("Notaus: main.py loeschen", color = if (armMain) FpvColors.Error else MaterialTheme.colorScheme.onSurfaceVariant)
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Notaus Boot freigeben", style = MaterialTheme.typography.bodySmall)
                Switch(checked = armBoot, onCheckedChange = { armBoot = it })
            }
            OutlinedButton(onClick = { confirmBoot = true }, enabled = armBoot) {
                Text("Notaus: boot.py loeschen", color = if (armBoot) FpvColors.Error else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }

    if (confirmMain) {
        AlertDialog(
            onDismissRequest = { confirmMain = false },
            title = { Text("NOTAUS ausfuehren?") },
            text = { Text("main.py wird geloescht und der Pico startet neu.") },
            confirmButton = { TextButton(onClick = { confirmMain = false; onDeleteMain() }) { Text("Loeschen") } },
            dismissButton = { TextButton(onClick = { confirmMain = false }) { Text("Abbrechen") } },
        )
    }
    if (confirmBoot) {
        AlertDialog(
            onDismissRequest = { confirmBoot = false },
            title = { Text("NOTAUS ausfuehren?") },
            text = { Text("boot.py wird geloescht und der Pico startet neu.") },
            confirmButton = { TextButton(onClick = { confirmBoot = false; onDeleteBoot() }) { Text("Loeschen") } },
            dismissButton = { TextButton(onClick = { confirmBoot = false }) { Text("Abbrechen") } },
        )
    }
}
