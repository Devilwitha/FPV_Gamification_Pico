package com.fpv.gamification.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Gleiche Markenfarben wie res/values/colors.xml + themes.xml - haelt die
 * Compose-Screens (PluginsActivity) optisch identisch zur WebView-basierten
 * MainActivity (siehe dortiges Theme.FpvGamification), statt der Compose-
 * Standardfarben.
 */
object FpvColors {
    val Background = Color(0xFF0B0E14)
    val Surface = Color(0xFF121722)
    val Primary = Color(0xFF39FF88)
    val PrimaryDark = Color(0xFF1FB866)
    val OnPrimary = Color(0xFF04140B)
    val Accent = Color(0xFF00D1FF)
    val Text = Color(0xFFE8F1FF)
    val TextMuted = Color(0xFF8CA0B3)
    val Error = Color(0xFFFF5C6C)
}

private val FpvColorScheme = darkColorScheme(
    primary = FpvColors.Primary,
    onPrimary = FpvColors.OnPrimary,
    primaryContainer = FpvColors.PrimaryDark,
    onPrimaryContainer = FpvColors.Text,
    secondary = FpvColors.Accent,
    onSecondary = FpvColors.OnPrimary,
    background = FpvColors.Background,
    onBackground = FpvColors.Text,
    surface = FpvColors.Surface,
    onSurface = FpvColors.Text,
    surfaceVariant = FpvColors.Surface,
    onSurfaceVariant = FpvColors.TextMuted,
    error = FpvColors.Error,
    onError = FpvColors.Text,
)

private val FpvShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

@Composable
fun FpvTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = FpvColorScheme,
        shapes = FpvShapes,
        content = content,
    )
}
