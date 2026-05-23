package com.meta.spatial.samples.mixedrealitytemplate.forge.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.AuthorKind
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.Risk

/**
 * Color + type tokens ported 1:1 from the iOS client's `Scene/PanelTheme.swift`
 * so the Quest panels read as the same product. Values are the iOS RGBA
 * constants converted to ARGB.
 */
object ForgeTheme {
    // Surfaces
    val panelBackground = Color(0xD9141414) // white 0.08 @ 85%
    val panelBackgroundLight = Color(0xE6242424) // white 0.14 @ 90%
    val sheetBackground = Color(0xF21A1A1A) // white 0.10 @ 95%
    val degradedBackground = Color(0xEB993311) // rust @ 92%

    // Text
    val primaryText = Color(0xFFFFFFFF)
    val secondaryText = Color(0xFFA6A6A6)
    val captionText = Color(0xFF737373)

    // Category accents
    val accentIC = Color(0xFFFF8C00) // ICs / SME orange
    val accentPassive = Color(0xFF00D9E6) // passives / user cyan
    val accentConnector = Color(0xFFE600E6) // connectors magenta
    val accentLive = Color(0xFF66D9FF) // live / streamed

    // Risk
    val riskLow = Color(0xFF00C75A)
    val riskMedium = Color(0xFFFFB800)
    val riskHigh = Color(0xFFF23319)

    fun authorColor(kind: AuthorKind): Color =
        when (kind) {
            AuthorKind.USER -> accentPassive
            AuthorKind.LIVE -> accentLive
            AuthorKind.SME -> accentIC
            AuthorKind.SYSTEM -> secondaryText
        }

    fun riskColor(risk: Risk): Color =
        when (risk) {
            Risk.LOW -> riskLow
            Risk.MEDIUM -> riskMedium
            Risk.HIGH -> riskHigh
        }

    // Type ramp (SF Pro on iOS → default family here; sizes/weights preserved)
    val hud = FontStyleSpec(11.sp, FontWeight.Medium, FontFamily.Monospace)
    val label = FontStyleSpec(11.sp, FontWeight.SemiBold, FontFamily.Monospace)
    val caption = FontStyleSpec(11.sp, FontWeight.Normal, FontFamily.Default)
    val body = FontStyleSpec(13.sp, FontWeight.Normal, FontFamily.Default)
    val headline = FontStyleSpec(15.sp, FontWeight.SemiBold, FontFamily.Default)

    val cornerRadius = 10.dp
    val pillCorner = 8.dp
    val panelPadding = 12.dp
}

data class FontStyleSpec(
    val size: androidx.compose.ui.unit.TextUnit,
    val weight: FontWeight,
    val family: FontFamily,
)
