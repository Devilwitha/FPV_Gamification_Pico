package com.fpv.gamification.app.ui.main

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.fpv.gamification.app.ui.plugins.PluginsScreen
import com.fpv.gamification.app.ui.plugins.PluginsScreenMode
import com.fpv.gamification.app.ui.plugins.PluginsViewModel

/**
 * Zwei Tabs: "Pico Plugins" (installierte Mods auf dem Geraet selbst
 * verwalten) und "Webshop Store" (im Webshop verfuegbare Mods durchsuchen
 * und einen Download auf den Pico ausloesen) - siehe PluginsViewModel fuer
 * den zugehoerigen State/die API-Aufrufe.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: PluginsViewModel = viewModel()) {
    val installedPlugins by viewModel.installedPlugins.collectAsState()
    val storePlugins by viewModel.storePlugins.collectAsState()
    val statusMessage by viewModel.statusMessage.collectAsState()
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        viewModel.refreshInstalled()
        viewModel.refreshStore()
    }

    LaunchedEffect(statusMessage) {
        statusMessage?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearStatusMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = { TopAppBar(title = { Text("Plugins & Store") }) },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            TabRow(selectedTabIndex = selectedTab) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    text = { Text("Pico Plugins") },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = {
                        selectedTab = 1
                        viewModel.refreshStore()
                    },
                    text = { Text("Webshop Store") },
                )
            }
            when (selectedTab) {
                0 -> PluginsScreen(
                    mode = PluginsScreenMode.INSTALLED,
                    installedPlugins = installedPlugins,
                    onToggle = viewModel::toggle,
                    onDelete = viewModel::delete,
                )
                else -> PluginsScreen(
                    mode = PluginsScreenMode.STORE,
                    storePlugins = storePlugins,
                    onDownload = viewModel::triggerDownload,
                )
            }
        }
    }
}
