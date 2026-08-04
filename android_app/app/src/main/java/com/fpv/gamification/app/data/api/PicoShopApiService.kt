package com.fpv.gamification.app.data.api

import com.google.gson.JsonObject
import com.google.gson.annotations.SerializedName
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.FieldMap
import retrofit2.http.FormUrlEncoded
import retrofit2.http.Field
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Url
import java.util.concurrent.TimeUnit

/**
 * Retrofit-Anbindung an zwei getrennte APIs:
 * - [PicoDeviceApi]: das lokale Pico-Geraet selbst (Standard-IP im
 *   Access-Point-Betrieb: 192.168.4.1, siehe strings.xml's
 *   webapp_base_url) - Plugin-Verwaltung, Firmware-Status, Store-Sync-Cache.
 * - [WebshopApi]: der zentrale Webshop-Plugin-Store (siehe
 *   webshop/app.py's /api/plugins) - Liste der dort verfuegbaren Mods.
 *
 * Response-Formen bewusst unterschiedlich abgebildet, weil die beiden
 * Server-Endpunkte unterschiedliche JSON-Strukturen liefern: der Pico
 * sendet die Plugin-Liste als reines JSON-Array (siehe
 * source/pico_web_api.py's handle_pico_api_route()), der Webshop als
 * {"plugins": [...]} (siehe webshop/app.py's api_plugins()).
 */

data class PluginStatus(
    val name: String,
    val version: String,
    val author: String = "",
    val enabled: Boolean,
    @SerializedName("has_error") val hasError: Boolean,
    @SerializedName("error_message") val errorMessage: String = "",
    val active: Boolean,
    // Siehe plugin_manager.list_plugins(): true, wenn das Plugin ein
    // manifest.json "ui_pages.main" deklariert (z.B. mods/shooter) - steuert,
    // ob die native System-Seite einen Link zur generisch gerenderten
    // PluginUiScreen fuer dieses Plugin anzeigt.
    @SerializedName("has_ui") val hasUi: Boolean = false,
)

data class StorePlugin(
    val name: String,
    val version: String,
    val author: String = "",
    val description: String = "",
    val files: List<String> = emptyList(),
)

data class StorePluginsResponse(
    val plugins: List<StorePlugin> = emptyList(),
)

data class FirmwareStatus(
    @SerializedName("fw_current_version") val currentVersion: String = "",
    @SerializedName("fw_latest_version") val latestVersion: String = "",
    @SerializedName("fw_update_available") val updateAvailable: Boolean = false,
    @SerializedName("wlan_connected") val wlanConnected: Boolean = false,
    @SerializedName("store_sync_ok") val storeSyncOk: Boolean = false,
    @SerializedName("store_error") val storeError: String = "",
)

data class SimpleOkResponse(
    val ok: Boolean,
    val error: String? = null,
)

// ==================== System-/Dashboard-Seite ====================
// Siehe source/admin_system.html + source/admin_dashboard.html + deren
// Backend (source/misc_routes_helpers.py, source/upload_helpers.py) - diese
// Endpunkte existierten bereits fuer die WebView-Oberflaeche und werden hier
// 1:1 fuer die native SystemScreen/DashboardScreen wiederverwendet, ohne
// Aenderungen am Pico-Code.

data class SystemInfo(
    @SerializedName("firmware_version") val firmwareVersion: String = "",
    val ssid: String = "",
    val ip: String = "",
    @SerializedName("mem_free") val memFree: Long = -1,
    @SerializedName("mem_alloc") val memAlloc: Long = -1,
    @SerializedName("fs_free") val fsFree: Long = -1,
    @SerializedName("fs_used") val fsUsed: Long = -1,
    @SerializedName("uptime_s") val uptimeS: Long = 0,
    @SerializedName("trick_tuning_profile") val trickTuningProfile: String = "",
    @SerializedName("ota_active") val otaActive: Boolean = false,
    @SerializedName("ota_total_chunks") val otaTotalChunks: Int = 0,
    @SerializedName("ota_received_chunks") val otaReceivedChunks: Int = 0,
    @SerializedName("hardware_id") val hardwareId: String = "",
    @SerializedName("device_role") val deviceRole: String = "",
    @SerializedName("board_type") val boardType: String = "",
    @SerializedName("license_status") val licenseStatus: String = "MISSING",
    @SerializedName("main_present") val mainPresent: Boolean = false,
    @SerializedName("boot_present") val bootPresent: Boolean = false,
    @SerializedName("developer_mode") val developerMode: Boolean = false,
)

data class DashboardData(
    val score: Int = 0,
    val highscore: Int = 0,
    @SerializedName("trick_tuning_profile") val trickTuningProfile: String = "",
)

data class NetworkConfig(
    val ok: Boolean = true,
    val ssid: String = "",
    val password: String = "",
)

data class LanguagePacksResponse(
    val ok: Boolean = true,
    val languages: List<String> = emptyList(),
    val current: String = "de",
)

data class MessageResponse(
    val ok: Boolean = false,
    val message: String? = null,
    val error: String? = null,
)

// Jede Log-Art hat ihre eigene Eintragsform (siehe admin_dashboard.html's
// renderStats()/renderActivity()) - ts_s/timestamp sind allen gemeinsam.
data class TrickLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    val score: Int = 0,
    val player: String = "",
)

data class ChallengeLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    val points: Int = 0,
    val description: String = "",
)

data class KothLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    val score: Int = 0,
)

data class RaceLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    @SerializedName("total_ms") val totalMs: Long? = null,
)

data class InfectionLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    val result: String = "",
)

data class ShooterLogEntry(
    @SerializedName("ts_s") val tsS: Long = 0,
    val timestamp: String = "",
    @SerializedName("hits_taken") val hitsTaken: Int = 0,
    @SerializedName("shots_fired") val shotsFired: Int = 0,
)

data class LogEnvelope<T>(val log: List<T> = emptyList())

/** API des lokalen Pico-Geraets (Access-Point-IP, siehe source/pico_web_api.py). */
interface PicoDeviceApi {
    @GET("api/plugins")
    suspend fun getInstalledPlugins(): List<PluginStatus>

    @FormUrlEncoded
    @POST("api/plugins/{name}/toggle")
    suspend fun togglePlugin(@Path("name") name: String, @Field("enabled") enabled: String): SimpleOkResponse

    @POST("api/plugins/{name}/delete")
    suspend fun deletePlugin(@Path("name") name: String): SimpleOkResponse

    @GET("api/firmware/status")
    suspend fun getFirmwareStatus(): FirmwareStatus

    @GET("api/store/list")
    suspend fun getCachedStoreList(): StorePluginsResponse

    @FormUrlEncoded
    @POST("api/store/download")
    suspend fun downloadPlugin(@Field("name") name: String): SimpleOkResponse

    // ---- System-Seite ----
    @GET("system-info")
    suspend fun getSystemInfo(): SystemInfo

    @GET("hotspot-config")
    suspend fun getHotspotConfig(): NetworkConfig

    @FormUrlEncoded
    @POST("set-hotspot-config")
    suspend fun setHotspotConfig(@Field("ssid") ssid: String, @Field("password") password: String): MessageResponse

    @GET("wlan-config")
    suspend fun getWlanConfig(): NetworkConfig

    @FormUrlEncoded
    @POST("set-wlan-config")
    suspend fun setWlanConfig(@Field("ssid") ssid: String, @Field("password") password: String): MessageResponse

    @GET("reset-device-role")
    suspend fun resetDeviceRole(@Query("confirm") confirm: String = "1"): MessageResponse

    @GET("restart-pico")
    suspend fun restartPico(): MessageResponse

    @GET("set-developer-mode")
    suspend fun setDeveloperMode(@Query("enabled") enabled: String): MessageResponse

    @GET("language-packs")
    suspend fun getLanguagePacks(): LanguagePacksResponse

    @GET("set-language")
    suspend fun setLanguage(@Query("lang") lang: String): MessageResponse

    @POST("clear-debug-log")
    suspend fun clearDebugLog(@Query("confirm") confirm: String = "1"): MessageResponse

    @POST("clear-session-log")
    suspend fun clearSessionLog(@Query("confirm") confirm: String = "1"): MessageResponse

    @GET("emergency-delete-main")
    suspend fun emergencyDeleteMain(@Query("confirm") confirm: String = "1"): MessageResponse

    @GET("emergency-delete-boot")
    suspend fun emergencyDeleteBoot(@Query("confirm") confirm: String = "1"): MessageResponse

    // license.lic/public_key.pem-Recovery-Upload (siehe upload_helpers.py):
    // gleicher Chunk-Upload-Mechanismus wie die WebView-Seite, siehe
    // SystemViewModel.uploadLicenseFile() fuer den Base64-Chunking-Client.
    @GET("prepare-upload")
    suspend fun prepareUpload(@Query("target") target: String, @Query("bundle_mode") bundleMode: String = "light"): MessageResponse

    @FormUrlEncoded
    @POST("upload-chunk")
    suspend fun uploadChunk(
        @Field("index") index: Int,
        @Field("total") total: Int,
        @Field("target") target: String,
        @Field("data") data: String,
    ): MessageResponse

    @GET("finalize-upload")
    suspend fun finalizeUpload(): MessageResponse

    // ---- Dashboard-Seite ----
    @GET("data")
    suspend fun getDashboardData(): DashboardData

    @GET("trick-highscore-log")
    suspend fun getTrickLog(): LogEnvelope<TrickLogEntry>

    @GET("challenge-log")
    suspend fun getChallengeLog(): LogEnvelope<ChallengeLogEntry>

    @GET("koth-log")
    suspend fun getKothLog(): LogEnvelope<KothLogEntry>

    @GET("race-log")
    suspend fun getRaceLog(): LogEnvelope<RaceLogEntry>

    @GET("infection-log")
    suspend fun getInfectionLog(): LogEnvelope<InfectionLogEntry>

    @GET("shooter-log")
    suspend fun getShooterLog(): LogEnvelope<ShooterLogEntry>

    @GET("reset-highscore")
    suspend fun resetHighscore(@Query("web") web: String = "1"): MessageResponse

    // ---- Generisches Plugin-UI-Schema (siehe plugin_manager.get_ui_schema(),
    // source/pico_web_api.py's "/api/plugin-ui/<name>") ----
    @GET("api/plugin-ui/{name}")
    suspend fun getPluginUiSchema(@Path("name") name: String): PluginUiSchemaResponse

    // Generische, schema-getriebene Aufrufe: das Plugin-UI-Schema beschreibt
    // NUR Felder/Buttons/Endpunkte (z.B. "/shooter-data", "/shooter-config"),
    // die eigentlichen Routen gehoeren weiterhin dem jeweiligen Plugin selbst
    // (siehe handle_plugin_route()) - dafuer braucht es dynamische @Url-
    // Aufrufe statt fest verdrahteter Methoden pro Plugin, sonst waere fuer
    // jedes neue Plugin wieder eigener Kotlin-Code noetig.
    @GET
    suspend fun getJson(@Url relativeUrl: String): JsonObject

    @FormUrlEncoded
    @POST
    suspend fun postForm(@Url relativeUrl: String, @FieldMap fields: Map<String, String>): JsonObject
}

/** API des zentralen Webshop-Plugin-Stores (siehe webshop/app.py). */
interface WebshopApi {
    @GET("api/plugins")
    suspend fun getStorePlugins(): StorePluginsResponse
}

object PicoShopApi {
    const val DEFAULT_WEBSHOP_BASE_URL = "http://46.4.78.34:5000/"

    /** Standard-Access-Point-IP des Pico (siehe strings.xml's webapp_base_url) -
     * zentral hier statt in einzelnen ViewModels dupliziert, da inzwischen
     * mehrere ViewModels (Plugins, System, Dashboard, PluginUi) denselben
     * Pico-Client bauen. */
    const val DEFAULT_PICO_BASE_URL = "http://192.168.4.1/"

    private fun buildRetrofit(baseUrl: String): Retrofit {
        val logging = HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
        val client = OkHttpClient.Builder()
            .addInterceptor(logging)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .build()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    fun createPicoDeviceApi(baseUrl: String): PicoDeviceApi =
        buildRetrofit(baseUrl).create(PicoDeviceApi::class.java)

    fun createWebshopApi(baseUrl: String = DEFAULT_WEBSHOP_BASE_URL): WebshopApi =
        buildRetrofit(baseUrl).create(WebshopApi::class.java)
}
