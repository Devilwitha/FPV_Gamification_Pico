package com.fpv.gamification.app

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.widget.EditText
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.fpv.gamification.app.databinding.ActivityMainBinding
import com.google.android.material.dialog.MaterialAlertDialogBuilder

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var hotspotConnector: HotspotConnector

    private val ssid: String by lazy { getString(R.string.hotspot_ssid) }
    private val password: String by lazy { getString(R.string.hotspot_password) }
    private val baseUrl: String by lazy { getString(R.string.webapp_base_url) }
    private val appHost: String by lazy { Uri.parse(baseUrl).host!! }

    private val locationPermissionRequest =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            startHotspotConnection()
        }

    // Fuer <input type="file"> in den Upload-Seiten (admin-update, admin-profiles, ...):
    // WebView unterstuetzt Datei-Auswahl nur, wenn onShowFileChooser() selbst einen
    // System-Datei-Picker startet und das Ergebnis zurueckreicht.
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val callback = filePathCallback
            filePathCallback = null
            val uris = if (result.resultCode == Activity.RESULT_OK) {
                WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
            } else {
                null
            }
            callback?.onReceiveValue(uris)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        hotspotConnector = HotspotConnector(this)

        setupWebView()

        binding.swipeRefresh.setOnRefreshListener { binding.webView.reload() }
        binding.retryButton.setOnClickListener { startHotspotConnection() }
        binding.wifiSettingsButton.setOnClickListener {
            startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.webView.canGoBack()) {
                    binding.webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        ensurePermissionsThenConnect()
    }

    private fun setupWebView() {
        val webView = binding.webView
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = true
            cacheMode = WebSettings.LOAD_DEFAULT
        }

        webView.webViewClient = PicoWebViewClient(
            context = this,
            appHost = appHost,
            onFinished = {
                binding.swipeRefresh.isRefreshing = false
                binding.progressBar.visibility = View.GONE
                showOverlay(false)
            },
            onMainFrameError = {
                binding.swipeRefresh.isRefreshing = false
                showOverlay(true, getString(R.string.status_failed_load))
            }
        )

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                binding.progressBar.progress = newProgress
                binding.progressBar.visibility =
                    if (newProgress in 1..99) View.VISIBLE else View.GONE
            }

            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams
            ): Boolean {
                this@MainActivity.filePathCallback?.onReceiveValue(null)
                this@MainActivity.filePathCallback = filePathCallback
                return try {
                    fileChooserLauncher.launch(fileChooserParams.createIntent())
                    true
                } catch (e: ActivityNotFoundException) {
                    this@MainActivity.filePathCallback = null
                    false
                }
            }

            // Die Pico-Seiten nutzen echte window.alert()/confirm()/prompt() (z.B. der
            // Piloten-Name beim neuen Highscore, Loeschbestaetigungen im Admin-Bereich).
            // Ohne diese drei Overrides zeigt eine WebView dafuer standardmaessig GAR
            // NICHTS an (JS bekommt sofort "abgebrochen" zurueck) - hier als native,
            // zum Dark-Theme passende Dialoge nachgebaut.
            override fun onJsAlert(
                view: WebView,
                url: String?,
                message: String?,
                result: JsResult
            ): Boolean {
                MaterialAlertDialogBuilder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }

            override fun onJsConfirm(
                view: WebView,
                url: String?,
                message: String?,
                result: JsResult
            ): Boolean {
                MaterialAlertDialogBuilder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }

            override fun onJsPrompt(
                view: WebView,
                url: String?,
                message: String?,
                defaultValue: String?,
                result: JsPromptResult
            ): Boolean {
                val padding = (16 * resources.displayMetrics.density).toInt()
                val input = EditText(this@MainActivity).apply {
                    setText(defaultValue)
                }
                val container = FrameLayout(this@MainActivity).apply {
                    setPadding(padding, padding / 2, padding, 0)
                    addView(input)
                }
                MaterialAlertDialogBuilder(this@MainActivity)
                    .setMessage(message)
                    .setView(container)
                    .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm(input.text.toString()) }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }
        }
    }

    private fun ensurePermissionsThenConnect() {
        val needsLocation = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) !=
            PackageManager.PERMISSION_GRANTED

        if (needsLocation) {
            locationPermissionRequest.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        } else {
            startHotspotConnection()
        }
    }

    private fun startHotspotConnection() {
        showOverlay(true, getString(R.string.status_connecting))
        hotspotConnector.connect(ssid, password, object : HotspotConnector.Callback {
            override fun onConnected() {
                showOverlay(true, getString(R.string.status_loading))
                binding.webView.loadUrl(baseUrl)
            }

            override fun onFailed(reason: String) {
                showOverlay(true, reason)
            }
        })
    }

    private fun showOverlay(visible: Boolean, message: String? = null) {
        binding.connectionOverlay.visibility = if (visible) View.VISIBLE else View.GONE
        if (message != null) {
            binding.statusText.text = message
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_reload -> {
                binding.webView.reload()
                true
            }
            R.id.action_browser -> {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(binding.webView.url ?: baseUrl)))
                true
            }
            R.id.action_wifi -> {
                startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }

    override fun onDestroy() {
        hotspotConnector.release()
        super.onDestroy()
    }
}
