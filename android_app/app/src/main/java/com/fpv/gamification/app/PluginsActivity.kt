package com.fpv.gamification.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.fpv.gamification.app.ui.main.MainScreen
import com.fpv.gamification.app.ui.theme.FpvTheme

/**
 * Compose-Einstiegspunkt fuer den nativen App-Bereich (Dashboard, System,
 * Plugins, Webshop Store - siehe [com.fpv.gamification.app.ui.main.MainScreen]) -
 * separat von der bestehenden WebView-MainActivity gehalten (siehe dortiger
 * Menüeintrag "Dashboard", der diese Activity startet), damit die
 * bewaehrte WebView-Oberflaeche (Race/KOTH/Infection/... - noch nicht nativ
 * nachgebaut) unangetastet bleibt. Nutzt FpvTheme statt des reinen
 * Compose-Standard-Themes, damit dieser Screen optisch zur restlichen App passt.
 */
class PluginsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            FpvTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    MainScreen(onBack = { finish() })
                }
            }
        }
    }
}
