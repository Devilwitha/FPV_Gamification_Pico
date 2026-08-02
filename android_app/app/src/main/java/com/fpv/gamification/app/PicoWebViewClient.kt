package com.fpv.gamification.app

import android.content.Context
import android.content.Intent
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.IOException

/**
 * Liefert die bekannten Pico-Seiten aus dem lokalen App-Bundle (assets/pico/),
 * statt sie bei jeder Navigation ueber den RAM-limitierten Pico-Webserver zu
 * laden - das macht die Oberflaeche selbst spuerbar schneller/robuster.
 *
 * Nur die statische Seiten-Huelle ist lokal gebuendelt: JEDE echte Daten-
 * /Aktions-Anfrage (fetch/XHR/POST der Seiten, z.B. /data, /system-info,
 * /upload-chunk, /set-trick-profile, ...) wird hier bewusst NICHT abgefangen
 * und geht dadurch ganz normal live an den Pico raus - gleiche Origin wie
 * eh schon (http://192.168.4.1), also kein CORS-Sonderfall und keine
 * Einschraenkung durch shouldInterceptRequest() (das den POST-Body nicht
 * liefert, siehe Android-Plattformlimitierung). Ergebnis: alle Web-Seiten
 * laufen nativ aus der App, nur die Live-Infos kommen vom Pico.
 */
class PicoWebViewClient(
    private val context: Context,
    private val appHost: String,
    private val onFinished: () -> Unit,
    private val onMainFrameError: (WebResourceRequest) -> Unit
) : WebViewClient() {

    companion object {
        // Route -> gebuendelte HTML-Datei (assets/pico/), siehe source/main.py,
        // misc_routes_helpers.py und gmr.py fuer die Original-Routentabelle.
        private val PAGE_ASSETS = mapOf(
            "/" to "index.html",
            "/index.html" to "index.html",
            "/admin" to "admin_dashboard.html",
            "/admin-update" to "admin_update.html",
            "/admin-simulate" to "admin_simulate.html",
            "/admin-idcard" to "admin_idcard.html",
            "/admin-challenges" to "admin_challenges.html",
            "/admin-infection" to "admin_infection.html",
            "/admin-credits" to "admin_credits.html",
            "/admin-profiles" to "admin_profiles.html",
            "/admin-system" to "admin_system.html",
            "/admin-koth" to "admin_koth.html",
            "/admin-race" to "admin_race.html",
            "/challenges-view" to "challenges_view.html",
            "/infection-view" to "infection_view.html",
            "/gamemodes-view" to "gamemodes_view.html"
        )
    }

    override fun shouldInterceptRequest(
        view: WebView,
        request: WebResourceRequest
    ): WebResourceResponse? {
        if (!request.isForMainFrame || request.method != "GET") return null
        if (request.url.host != appHost) return null

        val assetName = PAGE_ASSETS[request.url.path] ?: return null
        return try {
            WebResourceResponse("text/html", "utf-8", context.assets.open("pico/$assetName"))
        } catch (e: IOException) {
            null
        }
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        return if (request.url.host == appHost) {
            false
        } else {
            context.startActivity(Intent(Intent.ACTION_VIEW, request.url))
            true
        }
    }

    override fun onPageFinished(view: WebView, url: String?) {
        onFinished()
    }

    override fun onReceivedError(
        view: WebView,
        request: WebResourceRequest,
        error: WebResourceError
    ) {
        if (request.isForMainFrame) {
            onMainFrameError(request)
        }
    }
}
