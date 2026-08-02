package com.fpv.gamification.app

import android.app.Activity
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiConfiguration
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build

/**
 * Verbindet das Geraet automatisch mit dem WLAN-Hotspot des Pico.
 * Ab Android 10 (Q) laeuft das ueber WifiNetworkSpecifier + bindProcessToNetwork,
 * da das AP-Netz kein Internet hat und sonst vom System sofort wieder verlassen wird.
 * Auf aelteren Versionen wird die (deprecated) WifiConfiguration-API als Fallback genutzt.
 */
class HotspotConnector(private val activity: Activity) {

    interface Callback {
        fun onConnected()
        fun onFailed(reason: String)
    }

    private val connectivityManager =
        activity.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    fun connect(ssid: String, password: String, callback: Callback) {
        release()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            connectModern(ssid, password, callback)
        } else {
            connectLegacy(ssid, password, callback)
        }
    }

    private fun connectModern(ssid: String, password: String, callback: Callback) {
        val specifier = WifiNetworkSpecifier.Builder()
            .setSsid(ssid)
            .setWpa2Passphrase(password)
            .build()

        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .setNetworkSpecifier(specifier)
            .build()

        val callbackImpl = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                connectivityManager.bindProcessToNetwork(network)
                activity.runOnUiThread { callback.onConnected() }
            }

            override fun onUnavailable() {
                activity.runOnUiThread {
                    callback.onFailed("Hotspot \"$ssid\" nicht gefunden oder Verbindung abgelehnt.")
                }
            }

            override fun onLost(network: Network) {
                connectivityManager.bindProcessToNetwork(null)
            }
        }
        networkCallback = callbackImpl
        connectivityManager.requestNetwork(request, callbackImpl, 15000)
    }

    @Suppress("DEPRECATION")
    private fun connectLegacy(ssid: String, password: String, callback: Callback) {
        val wifiManager =
            activity.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        if (!wifiManager.isWifiEnabled) {
            wifiManager.isWifiEnabled = true
        }

        val currentSsid = wifiManager.connectionInfo?.ssid?.trim('"')
        if (currentSsid == ssid) {
            callback.onConnected()
            return
        }

        val config = WifiConfiguration().apply {
            SSID = "\"$ssid\""
            preSharedKey = "\"$password\""
            allowedKeyManagement.set(WifiConfiguration.KeyMgmt.WPA_PSK)
        }

        val netId = wifiManager.addNetwork(config)
        if (netId == -1) {
            callback.onFailed("WLAN-Profil fuer \"$ssid\" konnte nicht angelegt werden.")
            return
        }

        wifiManager.disconnect()
        wifiManager.enableNetwork(netId, true)
        wifiManager.reconnect()
        // Legacy-API liefert keinen zuverlaessigen Callback: optimistisch weitermachen,
        // die WebView zeigt einen Ladefehler an, falls die Verbindung doch nicht klappt.
        callback.onConnected()
    }

    fun release() {
        networkCallback?.let {
            try {
                connectivityManager.unregisterNetworkCallback(it)
            } catch (_: IllegalArgumentException) {
                // war bereits nicht (mehr) registriert
            }
        }
        networkCallback = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            connectivityManager.bindProcessToNetwork(null)
        }
    }
}
