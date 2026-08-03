package com.fpv.gamification.app.data.api

import com.google.gson.annotations.SerializedName
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.FormUrlEncoded
import retrofit2.http.Field
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
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
}

/** API des zentralen Webshop-Plugin-Stores (siehe webshop/app.py). */
interface WebshopApi {
    @GET("api/plugins")
    suspend fun getStorePlugins(): StorePluginsResponse
}

object PicoShopApi {
    const val DEFAULT_WEBSHOP_BASE_URL = "http://46.4.78.34:5000/"

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
