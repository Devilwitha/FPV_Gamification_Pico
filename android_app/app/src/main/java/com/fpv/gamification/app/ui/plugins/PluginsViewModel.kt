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
    picoBaseUrl: String = PicoShopApi.DEFAULT_PICO_BASE_URL,
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

    // Getrennt vom Snackbar-statusMessage: haelt fest, WARUM eine Liste
    // gerade leer ist (noch nie geladen / laedt / Fehler), damit die UI
    // einen leeren Bildschirm nie einfach unkommentiert stehen laesst.
    private val _isLoadingInstalled = MutableStateFlow(false)
    val isLoadingInstalled: StateFlow<Boolean> = _isLoadingInstalled.asStateFlow()

    private val _installedError = MutableStateFlow<String?>(null)
    val installedError: StateFlow<String?> = _installedError.asStateFlow()

    private val _isLoadingStore = MutableStateFlow(false)
    val isLoadingStore: StateFlow<Boolean> = _isLoadingStore.asStateFlow()

    private val _storeError = MutableStateFlow<String?>(null)
    val storeError: StateFlow<String?> = _storeError.asStateFlow()

    fun refreshInstalled() {
        viewModelScope.launch {
            _isLoadingInstalled.value = true
            try {
                _installedPlugins.value = picoApi.getInstalledPlugins()
                _installedError.value = null
            } catch (e: Exception) {
                _installedError.value = "Pico nicht erreichbar: ${e.message}"
            } finally {
                _isLoadingInstalled.value = false
            }
        }
    }

    fun refreshStore() {
        viewModelScope.launch {
            _isLoadingStore.value = true
            try {
                // Gewuenschter Ablauf: Store-Liste immer direkt vom Webshop laden.
                _storePlugins.value = webshopApi.getStorePlugins().plugins
                _storeError.value = null
            } catch (e: Exception) {
                _storeError.value = "Webshop nicht erreichbar: ${e.message}"
            } finally {
                _isLoadingStore.value = false
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
}
