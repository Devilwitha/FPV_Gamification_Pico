package com.fpv.gamification.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.fpv.gamification.app.ui.main.MainScreen

/**
 * Compose-Einstiegspunkt fuer die Plugin-/Store-Verwaltung - separat von
 * der bestehenden WebView-MainActivity gehalten (siehe dortiger Menüeintrag
 * "Plugins & Store", der diese Activity startet), damit die bewaehrte
 * WebView-Oberflaeche unangetastet bleibt.
 */
class PluginsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    MainScreen()
                }
            }
        }
    }
}
