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
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.fpv.gamification.app.databinding.ActivityMainBinding

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
