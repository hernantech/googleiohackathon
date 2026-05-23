package com.meta.spatial.samples.mixedrealitytemplate.forge.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.Risk
import com.meta.spatial.samples.mixedrealitytemplate.forge.state.ConfirmationUi
import com.meta.spatial.samples.mixedrealitytemplate.forge.state.SessionState
import kotlinx.coroutines.delay

/**
 * Operator-instruction confirmation. Mirrors the iOS `ConfirmationSheet`:
 * risk-colored header, the ActionCard body + diff, and "I did it" / "Skip"
 * buttons. The affirm button is armed after a risk-proportional delay
 * (LOW=0, MEDIUM=800ms, HIGH=2000ms) so HIGH-risk steps can't be rubber-stamped.
 */
@Composable
fun ConfirmationPanel(session: SessionState) {
    val pending by session.confirmation.collectAsState()
    val c = pending

    Box(
        modifier =
            Modifier.fillMaxSize()
                .clip(RoundedCornerShape(ForgeTheme.cornerRadius))
                .background(ForgeTheme.sheetBackground)
                .padding(ForgeTheme.panelPadding),
        contentAlignment = Alignment.Center,
    ) {
        if (c == null) {
            ForgeCaption("No pending confirmation.", ForgeTheme.captionText)
        } else {
            ConfirmationBody(c, session)
        }
    }
}

@Composable
private fun ConfirmationBody(c: ConfirmationUi, session: SessionState) {
    val armingMs = when (c.risk) {
        Risk.LOW -> 0L
        Risk.MEDIUM -> 800L
        Risk.HIGH -> 2000L
    }
    var remainingMs by remember(c.callId) { mutableLongStateOf(armingMs) }
    LaunchedEffect(c.callId) {
        while (remainingMs > 0) {
            delay(100)
            remainingMs = (remainingMs - 100).coerceAtLeast(0)
        }
    }
    val armed = remainingMs <= 0L
    val risk = ForgeTheme.riskColor(c.risk)

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            RiskPill(c.risk)
            androidx.compose.foundation.layout.Spacer(Modifier.fillMaxWidth().weight(1f))
            c.invokerSmeId?.let { ForgeCaption(it, ForgeTheme.accentIC) }
        }

        ForgeLabel(c.card?.title ?: "Forge asks you to:", ForgeTheme.primaryText)
        ForgeBody(c.card?.bodyMarkdown ?: c.summary, ForgeTheme.primaryText, maxLines = 6)
        c.card?.diffMarkdown?.let { ForgeCaption(it, ForgeTheme.secondaryText) }
        c.card?.documentedLimit?.let { ForgeCaption("⚑ $it", risk) }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = { session.respondConfirmation(false) },
                modifier = Modifier.weight(1f),
                colors =
                    ButtonDefaults.buttonColors(
                        containerColor = ForgeTheme.riskHigh.copy(alpha = 0.22f),
                        contentColor = ForgeTheme.primaryText,
                    ),
            ) {
                Text(c.card?.denyLabel ?: "Skip")
            }
            Button(
                onClick = { if (armed) session.respondConfirmation(true) },
                enabled = armed,
                modifier = Modifier.weight(1f),
                colors =
                    ButtonDefaults.buttonColors(
                        containerColor = ForgeTheme.riskLow.copy(alpha = 0.30f),
                        contentColor = ForgeTheme.primaryText,
                        disabledContainerColor = ForgeTheme.captionText.copy(alpha = 0.25f),
                        disabledContentColor = ForgeTheme.secondaryText,
                    ),
            ) {
                val affirm = c.card?.affirmLabel ?: "I did it"
                Text(if (armed) affirm else "$affirm (${remainingMs / 1000 + 1}s)")
            }
        }
    }
}

@Composable
private fun RiskPill(risk: Risk) {
    val color = ForgeTheme.riskColor(risk)
    Box(
        modifier =
            Modifier.clip(RoundedCornerShape(ForgeTheme.pillCorner))
                .background(color.copy(alpha = 0.22f))
                .padding(horizontal = 8.dp, vertical = 2.dp),
    ) {
        Text(
            risk.name,
            color = color,
            fontSize = ForgeTheme.label.size,
            fontWeight = ForgeTheme.label.weight,
            fontFamily = ForgeTheme.label.family,
        )
    }
}
