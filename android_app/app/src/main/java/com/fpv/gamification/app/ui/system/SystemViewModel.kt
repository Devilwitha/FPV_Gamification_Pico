package com.fpv.gamification.app.ui.system

import android.content.Context
import android.net.Uri
import android.util.Base64
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fpv.gamification.app.data.api.LanguagePacksResponse
import com.fpv.gamification.app.data.api.NetworkConfig
import com.fpv.gamification.app.data.api.PicoShopApi
import com.fpv.gamification.app.data.api.SystemInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Ein Plugin, das eine native UI-Seite anbietet (siehe PluginStatus.hasUi
 * und plugin_manager.get_ui_schema()) - wird auf der System-Seite gelistet,
 * Antippen oeffnet die generisch gerenderte PluginUiScreen. */
data class PluginUiEntry(val name: String, val title: String)

/**
 * State/Aktionen fuer die native SystemScreen - das Pendant zu
 * source/admin_system.html, spricht aber dieselben bereits bestehenden
 * JSON-Endpunkte des Pico direkt an (siehe source/misc_routes_helpers.py,
 * source/upload_helpers.py) statt eine WebView zu zeigen.
 */
class SystemViewModel(
    picoBaseUrl: String = PicoShopApi.DEFAULT_PICO_BASE_URL,
) : ViewModel() {

    private val api = PicoShopApi.createPicoDeviceApi(picoBaseUrl)

    private val _systemInfo = MutableStateFlow<SystemInfo?>(null)
    val systemInfo: StateFlow<SystemInfo?> = _systemInfo.asStateFlow()

    private val _hotspotConfig = MutableStateFlow(NetworkConfig())
    val hotspotConfig: StateFlow<NetworkConfig> = _hotspotConfig.asStateFlow()

    private val _wlanConfig = MutableStateFlow(NetworkConfig())
    val wlanConfig: StateFlow<NetworkConfig> = _wlanConfig.asStateFlow()

    private val _languages = MutableStateFlow(LanguagePacksResponse())
    val languages: StateFlow<LanguagePacksResponse> = _languages.asStateFlow()

    private val _pluginsWithUi = MutableStateFlow<List<PluginUiEntry>>(emptyList())
    val pluginsWithUi: StateFlow<List<PluginUiEntry>> = _pluginsWithUi.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    private val _statusMessage = MutableStateFlow<String?>(null)
    val statusMessage: StateFlow<String?> = _statusMessage.asStateFlow()

    fun refresh() {
        viewModelScope.launch {
            _isLoading.value = true
            try {
                _systemInfo.value = api.getSystemInfo()
                _errorMessage.value = null
            } catch (e: Exception) {
                _errorMessage.value = "Pico nicht erreichbar: ${e.message}"
            } finally {
                _isLoading.value = false
            }
            runCatching { _hotspotConfig.value = api.getHotspotConfig() }
            runCatching { _wlanConfig.value = api.getWlanConfig() }
            runCatching { _languages.value = api.getLanguagePacks() }
            runCatching {
                _pluginsWithUi.value = api.getInstalledPlugins()
                    .filter { it.active && it.hasUi }
                    .map { PluginUiEntry(it.name, it.name.replaceFirstChar(Char::uppercase)) }
            }
        }
    }

    fun saveHotspot(ssid: String, password: String, restart: Boolean) {
        val trimmedSsid = ssid.trim()
        if (trimmedSsid.isEmpty() || trimmedSsid.length > 32) {
            _statusMessage.value = "SSID muss 1 bis 32 Zeichen lang sein."
            return
        }
        if (password.length < 8 || password.length > 63) {
            _statusMessage.value = "Passwort muss 8 bis 63 Zeichen lang sein."
            return
        }
        viewModelScope.launch {
            try {
                val result = api.setHotspotConfig(trimmedSsid, password)
                _statusMessage.value = result.message ?: result.error ?: "Gespeichert."
                if (result.ok && restart) {
                    api.restartPico()
                }
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun saveWlan(ssid: String, password: String) {
        val trimmedSsid = ssid.trim()
        if (trimmedSsid.isEmpty() || trimmedSsid.length > 32) {
            _statusMessage.value = "SSID muss 1 bis 32 Zeichen lang sein."
            return
        }
        if (password.isNotEmpty() && (password.length < 8 || password.length > 63)) {
            _statusMessage.value = "Passwort muss leer (offenes WLAN) oder 8 bis 63 Zeichen lang sein."
            return
        }
        viewModelScope.launch {
            try {
                val result = api.setWlanConfig(trimmedSsid, password)
                _statusMessage.value = result.message ?: result.error ?: "Gespeichert."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun resetDeviceRole() {
        viewModelScope.launch {
            try {
                val result = api.resetDeviceRole()
                _statusMessage.value = result.message ?: result.error ?: "Zurueckgesetzt."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun restartPico() {
        viewModelScope.launch {
            try {
                api.restartPico()
                _statusMessage.value = "Neustart laeuft..."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun setDeveloperMode(enabled: Boolean) {
        viewModelScope.launch {
            try {
                val result = api.setDeveloperMode(if (enabled) "1" else "0")
                if (result.ok) {
                    refresh()
                } else {
                    _statusMessage.value = result.error ?: "Speichern fehlgeschlagen."
                }
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun setLanguage(lang: String) {
        viewModelScope.launch {
            try {
                val result = api.setLanguage(lang)
                _statusMessage.value = if (result.ok) "Sprache gespeichert." else "Speichern fehlgeschlagen."
                if (result.ok) refresh()
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun clearDebugLog() {
        viewModelScope.launch {
            try {
                val result = api.clearDebugLog()
                _statusMessage.value = result.message ?: result.error ?: "Log geloescht."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun clearSessionLog() {
        viewModelScope.launch {
            try {
                val result = api.clearSessionLog()
                _statusMessage.value = result.message ?: result.error ?: "Log geloescht."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun emergencyDeleteMain() {
        viewModelScope.launch {
            try {
                api.emergencyDeleteMain()
                _statusMessage.value = "Notaus Main laeuft..."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    fun emergencyDeleteBoot() {
        viewModelScope.launch {
            try {
                api.emergencyDeleteBoot()
                _statusMessage.value = "Notaus Boot laeuft..."
            } catch (e: Exception) {
                _statusMessage.value = "Fehler: ${e.message}"
            }
        }
    }

    /** Chunk-Upload einer vom Nutzer per SAF-Dateiauswahl gewaehlten Datei
     * (license.lic/public_key.pem) - gleiches Protokoll wie
     * admin_system.html's uploadFile()/JS: Datei komplett Base64-kodieren,
     * in Text-Chunks aufteilen und nacheinander an /upload-chunk senden
     * (siehe upload_helpers.py's handle_upload_chunk() - haengt Chunks nur
     * als Text aneinander, Aufteilung an beliebiger Stelle ist unkritisch). */
    fun uploadLicenseFile(context: Context, uri: Uri, targetName: String) {
        viewModelScope.launch {
            _statusMessage.value = "Lade $targetName hoch..."
            try {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw IllegalStateException("Datei konnte nicht gelesen werden.")
                val base64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                val chunkSize = 4000
                val totalChunks = maxOf(1, (base64.length + chunkSize - 1) / chunkSize)

                val prepared = api.prepareUpload(target = targetName, bundleMode = "light")
                if (!prepared.ok) {
                    _statusMessage.value = prepared.error ?: "Vorbereitung fehlgeschlagen."
                    return@launch
                }

                for (index in 0 until totalChunks) {
                    val start = index * chunkSize
                    val end = minOf(start + chunkSize, base64.length)
                    val chunk = if (start < base64.length) base64.substring(start, end) else ""
                    val chunkResult = api.uploadChunk(index = index, total = totalChunks, target = targetName, data = chunk)
                    if (!chunkResult.ok) {
                        _statusMessage.value = chunkResult.error ?: "Chunk-Upload fehlgeschlagen."
                        return@launch
                    }
                }

                val result = api.finalizeUpload()
                _statusMessage.value = result.message ?: result.error ?: "Hochgeladen."
                if (result.ok) refresh()
            } catch (e: Exception) {
                _statusMessage.value = "Upload fehlgeschlagen: ${e.message}"
            }
        }
    }

    fun clearStatusMessage() {
        _statusMessage.value = null
    }
}
