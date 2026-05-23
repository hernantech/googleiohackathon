package com.meta.spatial.samples.mixedrealitytemplate.forge.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.BodyContentType
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeJson
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.ForgeMsg
import com.meta.spatial.samples.mixedrealitytemplate.forge.proto.SnapshotAnalysis
import com.meta.spatial.samples.mixedrealitytemplate.forge.state.SessionState
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Discord-style expert chat — the soul of Forge. Left channel rail, message
 * thread for the selected channel, quick-prompt chips, and a text input.
 * Mirrors the iOS `ExpertChatPanel`, reflowed to a single panel for Quest.
 */
@Composable
fun ChatPanel(session: SessionState) {
    val channels by session.channels.collectAsState()
    val messages by session.messages.collectAsState()
    val selected by session.selectedChannel.collectAsState()

    Row(
        modifier =
            Modifier.fillMaxSize()
                .clip(RoundedCornerShape(ForgeTheme.cornerRadius))
                .background(ForgeTheme.panelBackground),
    ) {
        // Channel rail
        Column(
            modifier =
                Modifier.width(96.dp)
                    .fillMaxHeight()
                    .background(ForgeTheme.sheetBackground)
                    .padding(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            channels.forEach { ch ->
                val isSel = ch.id == selected
                Row(
                    modifier =
                        Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(ForgeTheme.pillCorner))
                            .background(
                                if (isSel) ForgeTheme.panelBackgroundLight else Color.Transparent
                            )
                            .clickable { session.selectChannel(ch.id) }
                            .padding(horizontal = 6.dp, vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "${ch.icon ?: "•"} ${ch.title}",
                        color = if (isSel) ForgeTheme.primaryText else ForgeTheme.secondaryText,
                        fontSize = ForgeTheme.caption.size,
                        fontWeight = ForgeTheme.label.weight,
                        fontFamily = ForgeTheme.caption.family,
                        maxLines = 1,
                    )
                }
            }
        }

        // Thread + input
        Column(
            modifier = Modifier.fillMaxSize().padding(ForgeTheme.panelPadding),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            ForgeLabel(selected, ForgeTheme.primaryText)

            val thread = messages[selected].orEmpty()
            val listState = rememberLazyListState()
            LaunchedEffect(thread.size) {
                if (thread.isNotEmpty()) listState.animateScrollToItem(thread.size - 1)
            }
            LazyColumn(
                modifier = Modifier.fillMaxWidth().weight(1f),
                state = listState,
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(thread) { MessageRow(it) }
            }

            QuickPrompts(session)
            ChatInput(onSend = session::sendChat)
        }
    }
}

@Composable
private fun MessageRow(m: ForgeMsg.ChatMessage) {
    val accent = ForgeTheme.authorColor(m.authorKind)
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                m.authorId,
                color = accent,
                fontSize = ForgeTheme.label.size,
                fontWeight = ForgeTheme.label.weight,
                fontFamily = ForgeTheme.label.family,
            )
            androidx.compose.foundation.layout.Spacer(Modifier.width(8.dp))
            ForgeCaption(timeOf(m.ts) + if (m.streaming) " ·typing…" else "", ForgeTheme.captionText)
        }
        if (m.bodyContentType == BodyContentType.JSON) {
            JsonCard(m.body)
        } else {
            ForgeBody(m.body, ForgeTheme.primaryText)
        }
    }
}

/** Render the known typed cards carried in `application/json` chat bodies. */
@Composable
private fun JsonCard(body: String) {
    when {
        body.contains("\"kind\":\"SmeResponse\"") -> {
            val r =
                runCatching {
                        ForgeJson.decodeFromString(ForgeMsg.serializer(), body)
                            as? ForgeMsg.SmeResponse
                    }
                    .getOrNull()
            if (r != null) {
                Column {
                    ForgeBody("${r.claim}  (${(r.confidence * 100).toInt()}%)", ForgeTheme.accentIC)
                    ForgeCaption(r.rationale, ForgeTheme.secondaryText)
                    r.proposedActions.forEach {
                        ForgeCaption("→ ${it.tool}: ${it.instruction ?: it.rationale}", ForgeTheme.primaryText)
                    }
                }
            } else ForgeBody(body, ForgeTheme.primaryText)
        }
        body.contains("\"kind\":\"SnapshotAnalysis\"") -> {
            val s = runCatching { ForgeJson.decodeFromString(SnapshotAnalysis.serializer(), body) }.getOrNull()
            if (s != null) {
                Column {
                    ForgeCaption("📷 ${s.model}", ForgeTheme.accentLive)
                    ForgeBody(s.analysis, ForgeTheme.primaryText)
                }
            } else ForgeBody(body, ForgeTheme.primaryText)
        }
        else -> ForgeBody(body, ForgeTheme.secondaryText)
    }
}

@Composable
private fun QuickPrompts(session: SessionState) {
    val presets =
        listOf(
            "@power why won't the BQ79616 wake?" to "@power",
            "@firmware comms init check?" to "@firmware",
            "@signal noise on the bus?" to "@signal",
        )
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        presets.forEach { (text, label) ->
            Button(
                onClick = { session.sendChat(text) },
                colors =
                    ButtonDefaults.buttonColors(
                        containerColor = ForgeTheme.panelBackgroundLight,
                        contentColor = ForgeTheme.accentIC,
                    ),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 2.dp),
            ) {
                Text(label, fontSize = ForgeTheme.caption.size, fontFamily = ForgeTheme.label.family)
            }
        }
    }
}

@Composable
private fun ChatInput(onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            modifier = Modifier.weight(1f),
            placeholder = { Text("Ask the guild… (mention @power, @firmware)", color = ForgeTheme.captionText) },
            singleLine = true,
            colors =
                TextFieldDefaults.colors(
                    focusedTextColor = ForgeTheme.primaryText,
                    unfocusedTextColor = ForgeTheme.primaryText,
                    focusedContainerColor = ForgeTheme.panelBackgroundLight,
                    unfocusedContainerColor = ForgeTheme.panelBackgroundLight,
                ),
            keyboardOptions =
                androidx.compose.foundation.text.KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions =
                androidx.compose.foundation.text.KeyboardActions(
                    onSend = {
                        onSend(text)
                        text = ""
                    }
                ),
        )
        androidx.compose.foundation.layout.Spacer(Modifier.width(8.dp))
        Button(
            onClick = {
                onSend(text)
                text = ""
            },
            colors =
                ButtonDefaults.buttonColors(
                    containerColor = ForgeTheme.accentPassive.copy(alpha = 0.25f),
                    contentColor = ForgeTheme.accentPassive,
                ),
        ) {
            Text("Send")
        }
    }
}

private val TIME_FMT = SimpleDateFormat("HH:mm", Locale.US)

private fun timeOf(tsNs: Long): String = TIME_FMT.format(Date(tsNs / 1_000_000L))
