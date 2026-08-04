package com.fpv.gamification.app.ui.dashboard

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.fpv.gamification.app.data.api.ChallengeLogEntry
import com.fpv.gamification.app.data.api.InfectionLogEntry
import com.fpv.gamification.app.data.api.KothLogEntry
import com.fpv.gamification.app.data.api.PicoShopApi
import com.fpv.gamification.app.data.api.RaceLogEntry
import com.fpv.gamification.app.data.api.ShooterLogEntry
import com.fpv.gamification.app.data.api.TrickLogEntry
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Eine der sechs Statistik-Kacheln (siehe admin_dashboard.html's renderStats()). */
data class StatTile(val label: String, val value: String, val sub: String, val colorHex: String)

/** Ein Eintrag im gemeinsamen, nach Zeit sortierten Aktivitaets-Feed (siehe
 * admin_dashboard.html's renderActivity()). */
data class ActivityItem(val tsS: Long, val time: String, val colorHex: String, val text: String)

private fun fmtMs(ms: Long?): String {
    if (ms == null) return "-"
    val m = ms / 60000
    val s = (ms % 60000) / 1000
    val r = ms % 1000
    val base = if (m > 0) "$m:${s.toString().padStart(2, '0')}" else s.toString()
    return "$base.${r.toString().padStart(3, '0')}s"
}

private fun resultLabel(result: String): String = when (result) {
    "won" -> "Sieg"
    "lost" -> "Niederlage"
    "stopped" -> "Abgebrochen"
    else -> result
}

/**
 * State/Aktionen fuer die native DashboardScreen - das Pendant zu
 * source/admin_dashboard.html, spricht dieselben "/data"-, "/system-info"-
 * und "*-log"-Endpunkte direkt an.
 */
class DashboardViewModel(
    picoBaseUrl: String = PicoShopApi.DEFAULT_PICO_BASE_URL,
) : ViewModel() {

    private val api = PicoShopApi.createPicoDeviceApi(picoBaseUrl)

    private val _score = MutableStateFlow(0)
    val score: StateFlow<Int> = _score.asStateFlow()

    private val _highscore = MutableStateFlow(0)
    val highscore: StateFlow<Int> = _highscore.asStateFlow()

    private val _activeProfile = MutableStateFlow("-")
    val activeProfile: StateFlow<String> = _activeProfile.asStateFlow()

    private val _memFreeKb = MutableStateFlow<Double?>(null)
    val memFreeKb: StateFlow<Double?> = _memFreeKb.asStateFlow()

    private val _statTiles = MutableStateFlow<List<StatTile>>(emptyList())
    val statTiles: StateFlow<List<StatTile>> = _statTiles.asStateFlow()

    private val _activity = MutableStateFlow<List<ActivityItem>>(emptyList())
    val activity: StateFlow<List<ActivityItem>> = _activity.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    fun refresh() {
        refreshStatus()
        refreshStats()
    }

    private fun refreshStatus() {
        viewModelScope.launch {
            _isLoading.value = true
            try {
                val data = api.getDashboardData()
                _score.value = data.score
                _highscore.value = data.highscore
                _activeProfile.value = data.trickTuningProfile.ifBlank { "-" }
                _errorMessage.value = null
            } catch (e: Exception) {
                _errorMessage.value = "Pico nicht erreichbar: ${e.message}"
            } finally {
                _isLoading.value = false
            }
            runCatching {
                val info = api.getSystemInfo()
                _memFreeKb.value = if (info.memFree >= 0) info.memFree / 1024.0 else null
            }
        }
    }

    private fun refreshStats() {
        viewModelScope.launch {
            val trick = runCatching { api.getTrickLog().log }.getOrDefault(emptyList<TrickLogEntry>())
            val challenges = runCatching { api.getChallengeLog().log }.getOrDefault(emptyList<ChallengeLogEntry>())
            val koth = runCatching { api.getKothLog().log }.getOrDefault(emptyList<KothLogEntry>())
            val race = runCatching { api.getRaceLog().log }.getOrDefault(emptyList<RaceLogEntry>())
            val infection = runCatching { api.getInfectionLog().log }.getOrDefault(emptyList<InfectionLogEntry>())
            val shooter = runCatching { api.getShooterLog().log }.getOrDefault(emptyList<ShooterLogEntry>())

            val noRounds = "Noch keine Runde"
            val tiles = mutableListOf<StatTile>()

            tiles += if (trick.isNotEmpty()) {
                val best = trick.maxBy { it.score }
                StatTile("Trick-Highscore", "${best.score} Pkt", "${trick.size} Rekorde", "#e74c3c")
            } else StatTile("Trick-Highscore", noRounds, "", "#e74c3c")

            tiles += if (challenges.isNotEmpty()) {
                val sum = challenges.sumOf { it.points }
                StatTile("Challenges", "$sum Pkt", "${challenges.size} bestanden", "#8e44ad")
            } else StatTile("Challenges", noRounds, "", "#8e44ad")

            tiles += if (koth.isNotEmpty()) {
                val best = koth.maxBy { it.score }
                StatTile("King of the Hill", "${best.score} Pkt", "${koth.size} Runden", "#e6a23c")
            } else StatTile("King of the Hill", noRounds, "", "#e6a23c")

            tiles += if (race.isNotEmpty()) {
                val best = race.minByOrNull { it.totalMs ?: Long.MAX_VALUE }
                StatTile("Race", fmtMs(best?.totalMs), "${race.size} Runden", "#2980b9")
            } else StatTile("Race", noRounds, "", "#2980b9")

            tiles += if (infection.isNotEmpty()) {
                val wins = infection.count { it.result == "won" }
                StatTile("Infection", "$wins/${infection.size}", "Siege", "#d4473f")
            } else StatTile("Infection", noRounds, "", "#d4473f")

            tiles += if (shooter.isNotEmpty()) {
                val sum = shooter.sumOf { it.hitsTaken }
                StatTile("Shooter", "$sum Treffer", "${shooter.size} Runden", "#c0392b")
            } else StatTile("Shooter", noRounds, "", "#c0392b")

            _statTiles.value = tiles

            val items = mutableListOf<ActivityItem>()
            trick.forEach { items += ActivityItem(it.tsS, it.timestamp, "#e74c3c", "🏆 Neuer Highscore: ${it.score} Pkt (${it.player})") }
            challenges.forEach { items += ActivityItem(it.tsS, it.timestamp, "#8e44ad", "🎯 ${it.description} (+${it.points})") }
            koth.forEach { items += ActivityItem(it.tsS, it.timestamp, "#e6a23c", "👑 KOTH-Runde beendet: ${it.score} Pkt") }
            race.forEach { items += ActivityItem(it.tsS, it.timestamp, "#2980b9", "🏁 Rennen beendet: ${fmtMs(it.totalMs)}") }
            infection.forEach { items += ActivityItem(it.tsS, it.timestamp, "#d4473f", "☣️ Infection-Runde: ${resultLabel(it.result)}") }
            shooter.forEach {
                items += ActivityItem(
                    it.tsS, it.timestamp, "#c0392b",
                    "💥 Shooter-Runde beendet: ${it.hitsTaken} Treffer kassiert, ${it.shotsFired} Schuesse",
                )
            }
            _activity.value = items.sortedByDescending { it.tsS }.take(20)
        }
    }

    fun resetHighscore() {
        viewModelScope.launch {
            runCatching { api.resetHighscore() }
            refreshStatus()
        }
    }
}
