package com.fpv.gamification.app.ui.main

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Storefront
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.fpv.gamification.app.ui.dashboard.DashboardScreen
import com.fpv.gamification.app.ui.dashboard.DashboardViewModel
import com.fpv.gamification.app.ui.plugins.PluginUiScreen
import com.fpv.gamification.app.ui.plugins.PluginsScreen
import com.fpv.gamification.app.ui.plugins.PluginsScreenMode
import com.fpv.gamification.app.ui.plugins.PluginsViewModel
import com.fpv.gamification.app.ui.system.SystemScreen
import com.fpv.gamification.app.ui.system.SystemViewModel

private val TAB_TITLES = listOf("Dashboard", "System", "Pico Plugins", "Webshop Store")

/**
 * Nativer App-Bereich mit vier Tabs: Dashboard und System sind das native
 * Pendant zu source/admin_dashboard.html/admin_system.html (siehe
 * [DashboardScreen]/[SystemScreen]), Pico Plugins/Webshop Store verwalten
 * installierte Mods bzw. den zentralen Store (siehe [PluginsScreen] -
 * unveraendert). Farben/Formen kommen einheitlich aus FpvTheme (siehe
 * PluginsActivity), damit dieser Screen optisch zur WebView-MainActivity
 * passt.
 *
 * Waehlt der Nutzer auf der System-Seite ein Plugin mit nativer UI (z.B.
 * Shooter), "uebernimmt" [PluginUiScreen] den kompletten Bildschirm
 * (eigenes Scaffold, siehe [openPluginUi]-State) - kein zusaetzliches
 * Navigations-Framework noetig, da es sich um einen einzigen, flachen
 * "Vollbild-Modus" handelt.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    pluginsViewModel: PluginsViewModel = viewModel(),
    dashboardViewModel: DashboardViewModel = viewModel(),
    systemViewModel: SystemViewModel = viewModel(),
    onBack: () -> Unit = {},
) {
    var openPluginUi by remember { mutableStateOf<String?>(null) }
    val currentPluginUi = openPluginUi
    if (currentPluginUi != null) {
        PluginUiScreen(pluginName = currentPluginUi, onBack = { openPluginUi = null })
        return
    }

    val installedPlugins by pluginsViewModel.installedPlugins.collectAsState()
    val storePlugins by pluginsViewModel.storePlugins.collectAsState()
    val isLoadingInstalled by pluginsViewModel.isLoadingInstalled.collectAsState()
    val isLoadingStore by pluginsViewModel.isLoadingStore.collectAsState()
    val installedError by pluginsViewModel.installedError.collectAsState()
    val storeError by pluginsViewModel.storeError.collectAsState()
    val pluginsStatusMessage by pluginsViewModel.statusMessage.collectAsState()
    val systemStatusMessage by systemViewModel.statusMessage.collectAsState()
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        dashboardViewModel.refresh()
        systemViewModel.refresh()
        pluginsViewModel.refreshInstalled()
        pluginsViewModel.refreshStore()
    }

    // Gemeinsamer SnackbarHost fuer alle Tabs statt je eigenem: SystemScreen/
    // DashboardScreen haben (anders als PluginsScreen) kein eigenes Scaffold,
    // siehe [SystemViewModel.statusMessage].
    LaunchedEffect(pluginsStatusMessage) {
        pluginsStatusMessage?.let {
            snackbarHostState.showSnackbar(it)
            pluginsViewModel.clearStatusMessage()
        }
    }
    LaunchedEffect(systemStatusMessage) {
        systemStatusMessage?.let {
            snackbarHostState.showSnackbar(it)
            systemViewModel.clearStatusMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(TAB_TITLES[selectedTab]) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Zurück")
                    }
                },
                actions = {
                    IconButton(onClick = {
                        when (selectedTab) {
                            0 -> dashboardViewModel.refresh()
                            1 -> systemViewModel.refresh()
                            2 -> pluginsViewModel.refreshInstalled()
                            else -> pluginsViewModel.refreshStore()
                        }
                    }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Aktualisieren")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                    navigationIconContentColor = MaterialTheme.colorScheme.onSurface,
                    actionIconContentColor = MaterialTheme.colorScheme.onSurface,
                ),
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            TabRow(
                selectedTabIndex = selectedTab,
                containerColor = MaterialTheme.colorScheme.surface,
                contentColor = MaterialTheme.colorScheme.primary,
                indicator = { _ ->
                    TabRowDefaults.SecondaryIndicator(
                        modifier = Modifier,
                        color = MaterialTheme.colorScheme.primary,
                    )
                },
            ) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    icon = { Icon(Icons.Filled.Dashboard, contentDescription = null) },
                    text = { Text("Dashboard") },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    icon = { Icon(Icons.Filled.Settings, contentDescription = null) },
                    text = { Text("System") },
                )
                Tab(
                    selected = selectedTab == 2,
                    onClick = { selectedTab = 2 },
                    icon = { Icon(Icons.Filled.Extension, contentDescription = null) },
                    text = { Text("Plugins") },
                )
                Tab(
                    selected = selectedTab == 3,
                    onClick = {
                        selectedTab = 3
                        pluginsViewModel.refreshStore()
                    },
                    icon = { Icon(Icons.Filled.Storefront, contentDescription = null) },
                    text = { Text("Store") },
                )
            }
            when (selectedTab) {
                0 -> DashboardScreen(viewModel = dashboardViewModel)
                1 -> SystemScreen(viewModel = systemViewModel, onOpenPluginUi = { name -> openPluginUi = name })
                2 -> PluginsScreen(
                    mode = PluginsScreenMode.INSTALLED,
                    installedPlugins = installedPlugins,
                    isLoading = isLoadingInstalled,
                    errorMessage = installedError,
                    onRetry = pluginsViewModel::refreshInstalled,
                    onToggle = pluginsViewModel::toggle,
                    onDelete = pluginsViewModel::delete,
                )
                else -> PluginsScreen(
                    mode = PluginsScreenMode.STORE,
                    storePlugins = storePlugins,
                    isLoading = isLoadingStore,
                    errorMessage = storeError,
                    onRetry = pluginsViewModel::refreshStore,
                    onDownload = pluginsViewModel::triggerDownload,
                )
            }
        }
    }
}
