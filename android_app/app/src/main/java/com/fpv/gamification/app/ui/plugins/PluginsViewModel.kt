package com.fpv.gamification.app.ui.plugins

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fpv.gamification.app.data.api.PicoShopApi
import com.fpv.gamification.app.data.api.PluginStatus
import com.fpv.gamification.app.data.api.StorePlugin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Haelt den State fuer beide Tabs von [com.fpv.gamification.app.ui.main.MainScreen]:
 * installierte Plugins (vom Pico selbst) und Store-Plugins (vom zentralen
 * Webshop) - siehe [PicoShopApi] fuer die beiden getrennten API-Clients.
 */
class PluginsViewModel(
    picoBaseUrl: String = DEFAULT_PICO_BASE_URL,
    webshopBaseUrl: String = PicoShopApi.DEFAULT_WEBSHOP_BASE_URL,
) : ViewModel() {

    private val picoApi = PicoShopApi.createPicoDeviceApi(picoBaseUrl)
    private val webshopApi = PicoShopApi.createWebshopApi(webshopBaseUrl)

    private val _installedPlugins = MutableStateFlow<List<PluginStatus>>(emptyList())
    val installedPlugins: StateFlow<List<PluginStatus>> = _installedPlugins.asStateFlow()

    private val _storePlugins = MutableStateFlow<List<StorePlugin>>(emptyList())
    val storePlugins: StateFlow<List<StorePlugin>> = _storePlugins.asStateFlow()

    private val _statusMessage = MutableStateFlow<String?>(null)
    val statusMessage: StateFlow<String?> = _statusMessage.asStateFlow()

    fun refreshInstalled() {
        viewModelScope.launch {
            try {
                _installedPlugins.value = picoApi.getInstalledPlugins()
            } catch (e: Exception) {
                _statusMessage.value = "Pico nicht erreichbar: ${e.message}"
            }
        }
    }

    fun refreshStore() {
        viewModelScope.launch {
            try {
                // Gewuenschter Ablauf: Store-Liste immer direkt vom Webshop laden.
                _storePlugins.value = webshopApi.getStorePlugins().plugins
            } catch (e: Exception) {
                _statusMessage.value = "Webshop nicht erreichbar: ${e.message}"
            }
        }
    }

    fun toggle(name: String, enabled: Boolean) {
        viewModelScope.launch {
            try {
                picoApi.togglePlugin(name, if (enabled) "1" else "0")
                refreshInstalled()
            } catch (e: Exception) {
                _statusMessage.value = "Aktion fehlgeschlagen: ${e.message}"
            }
        }
    }

    fun delete(name: String) {
        viewModelScope.launch {
            try {
                picoApi.deletePlugin(name)
                refreshInstalled()
            } catch (e: Exception) {
                _statusMessage.value = "Löschen fehlgeschlagen: ${e.message}"
            }
        }
    }

    fun triggerDownload(name: String) {
        viewModelScope.launch {
            try {
                // Download wird auf dem Pico ausgefuehrt; daher erst Pico-Verbindung pruefen.
                picoApi.getFirmwareStatus()
                val result = picoApi.downloadPlugin(name)
                _statusMessage.value = if (result.ok) {
                    "Download auf dem Pico gestartet für '$name'."
                } else {
                    "Fehler: ${result.error ?: "unbekannt"}"
                }
            } catch (e: Exception) {
                _statusMessage.value = "Download nur mit verbundenem Pico moeglich (Hotspot + WLAN am Pico). Details: ${e.message}"
            }
        }
    }

    fun clearStatusMessage() {
        _statusMessage.value = null
    }

    companion object {
        /** Standard-Access-Point-IP des Pico (siehe strings.xml's webapp_base_url). */
        const val DEFAULT_PICO_BASE_URL = "http://192.168.4.1/"
    }
}
