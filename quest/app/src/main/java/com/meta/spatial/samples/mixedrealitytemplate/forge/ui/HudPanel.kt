package com.meta.spatial.samples.mixedrealitytemplate.forge.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.meta.spatial.samples.mixedrealitytemplate.forge.net.SocketState
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.Speaker
import com.meta.spatial.samples.mixedrealitytemplate.forge.state.SessionState

/**
 * Top status panel: connection dot + label, last transcript line, and the
 * tool-call ticker. Mirrors the iOS `HudOverlay`.
 */
@Composable
fun HudPanel(session: SessionState) {
    val conn by session.connection.collectAsState()
    val transcript by session.transcript.collectAsState()
    val tools by session.toolCalls.collectAsState()
    val live by session.liveActive.collectAsState()

    Column(
        modifier =
            Modifier.fillMaxSize()
                .clip(RoundedCornerShape(ForgeTheme.cornerRadius))
                .background(ForgeTheme.panelBackground)
                .padding(ForgeTheme.panelPadding),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val (dot, label) = connectionVisual(conn)
            Box(dot)
            Spacer8()
            ForgeLabel("FORGE · $label", ForgeTheme.primaryText)
            if (live) {
                Spacer8()
                ForgeLabel("● LIVE", ForgeTheme.riskHigh)
            }
        }

        val t = transcript
        if (t != null) {
            val who = if (t.speaker == Speaker.USER) "you" else "forge"
            ForgeBody(
                "$who: ${t.text}",
                if (t.speaker == Speaker.USER) ForgeTheme.accentPassive else ForgeTheme.accentLive,
                maxLines = 2,
            )
        }

        if (tools.isNotEmpty()) {
            ForgeCaption("tool calls", ForgeTheme.captionText)
            tools.takeLast(4).forEach { tc ->
                val mark = if (tc.inFlight) "▶" else "✓"
                ForgeCaption(
                    "$mark ${tc.name} ${tc.summary}",
                    if (tc.inFlight) ForgeTheme.accentIC else ForgeTheme.secondaryText,
                )
            }
        }
    }
}

private fun connectionVisual(state: SocketState): Pair<Modifier, String> {
    val (color, text) =
        when (state) {
            is SocketState.Open -> ForgeTheme.riskLow to "connected"
            is SocketState.Connecting -> ForgeTheme.riskMedium to "connecting…"
            is SocketState.Degraded -> ForgeTheme.riskMedium to "reconnecting (${state.retryInSec}s)"
            is SocketState.Closed -> ForgeTheme.riskHigh to "offline"
        }
    return Modifier.size(10.dp).clip(CircleShape).background(color) to text
}

@Composable private fun Box(modifier: Modifier) = androidx.compose.foundation.layout.Box(modifier)

@Composable private fun Spacer8() = androidx.compose.foundation.layout.Spacer(Modifier.size(8.dp))

@Composable
fun ForgeLabel(text: String, color: Color) =
    Text(
        text,
        color = color,
        fontSize = ForgeTheme.label.size,
        fontWeight = ForgeTheme.label.weight,
        fontFamily = ForgeTheme.label.family,
    )

@Composable
fun ForgeBody(text: String, color: Color, maxLines: Int = Int.MAX_VALUE) =
    Text(
        text,
        color = color,
        fontSize = ForgeTheme.body.size,
        fontWeight = ForgeTheme.body.weight,
        fontFamily = ForgeTheme.body.family,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )

@Composable
fun ForgeCaption(text: String, color: Color) =
    Text(
        text,
        color = color,
        fontSize = ForgeTheme.caption.size,
        fontWeight = ForgeTheme.caption.weight,
        fontFamily = ForgeTheme.caption.family,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
    )
