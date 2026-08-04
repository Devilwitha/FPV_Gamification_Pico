package com.fpv.gamification.app.ui.plugins

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fpv.gamification.app.data.api.PicoShopApi
import com.fpv.gamification.app.data.api.PluginUiSchema
import com.google.gson.JsonObject
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * State/Aktionen fuer die generische [PluginUiScreen]: laedt zuerst das
 * Schema eines Plugins ueber "/api/plugin-ui/<name>" (siehe
 * plugin_manager.get_ui_schema()), pollt danach dessen "poll_endpoint" in
 * regelmaessigen Abstaenden und leitet Formular-/Button-Aktionen generisch
 * an die vom Schema selbst genannten Endpunkte weiter - kein plugin-
 * spezifischer Code noetig, siehe Schema-Dokumentation in
 * data/api/PluginUiModels.kt.
 */
class PluginUiViewModel(
    private val pluginName: String,
    picoBaseUrl: String = PicoShopApi.DEFAULT_PICO_BASE_URL,
) : ViewModel() {

    private val api = PicoShopApi.createPicoDeviceApi(picoBaseUrl)

    private val _schema = MutableStateFlow<PluginUiSchema?>(null)
    val schema: StateFlow<PluginUiSchema?> = _schema.asStateFlow()

    private val _data = MutableStateFlow<JsonObject?>(null)
    val data: StateFlow<JsonObject?> = _data.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    private val _statusMessage = MutableStateFlow<String?>(null)
    val statusMessage: StateFlow<String?> = _statusMessage.asStateFlow()

    private var pollJob: Job? = null

    fun load() {
        if (_schema.value != null) return
        viewModelScope.launch {
            _isLoading.value = true
            try {
                val response = api.getPluginUiSchema(pluginName)
                val loadedSchema = response.schema
                if (response.ok && loadedSchema != null) {
                    _schema.value = loadedSchema
                    _errorMessage.value = null
                    startPolling(loadedSchema)
                } else {
                    _errorMessage.value = response.error ?: "Kein natives UI-Schema fuer dieses Plugin verfuegbar."
                }
            } catch (e: Exception) {
                _errorMessage.value = "Pico nicht erreichbar: ${e.message}"
            } finally {
                _isLoading.value = false
            }
        }
    }

    private fun startPolling(schema: PluginUiSchema) {
        val endpoint = schema.pollEndpoint ?: return
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (true) {
                runCatching { _data.value = api.getJson(endpoint) }
                delay(schema.pollIntervalMs.coerceAtLeast(200))
            }
        }
    }

    /** Sendet eine Formular-/Button-Aktion an den vom Schema genannten
     * Endpunkt und pollt danach sofort einmal neu, statt auf das naechste
     * reguläre Poll-Intervall zu warten (direktes Feedback im UI). */
    fun sendAction(endpoint: String, fields: Map<String, String> = emptyMap()) {
        viewModelScope.launch {
            try {
                api.postForm(endpoint, fields)
                schema.value?.pollEndpoint?.let { pollEndpoint ->
                    runCatching { _data.value = api.getJson(pollEndpoint) }
                }
            } catch (e: Exception) {
                _statusMessage.value = "Aktion fehlgeschlagen: ${e.message}"
            }
        }
    }

    fun clearStatusMessage() {
        _statusMessage.value = null
    }

    override fun onCleared() {
        pollJob?.cancel()
        super.onCleared()
    }
}
