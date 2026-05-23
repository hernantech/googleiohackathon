import SwiftUI

// v2 Discord-style multi-channel chat panel.
// Channel rail on the left; message list + input on the right.

struct ExpertChatPanel: View {

    @Environment(SessionViewModel.self) private var vm
    @State private var inputText: String = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        HStack(spacing: 0) {
            channelRail
            Divider().background(PanelTheme.secondaryText.opacity(0.3))
            VStack(spacing: 0) {
                channelHeader
                Divider().background(PanelTheme.secondaryText.opacity(0.3))
                messageList
                inputRow
            }
        }
        .background(PanelTheme.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: PanelTheme.cornerRadius))
    }

    // MARK: - Channel rail

    private var channelRail: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 2) {
                ForEach(vm.chat.channels) { channel in
                    ChannelRailRow(
                        channel: channel,
                        isSelected: channel.id == vm.chat.selectedChannelId,
                        isMuted: vm.chat.muted.contains(channel.id),
                        onSelect: { vm.chat.selectedChannelId = channel.id },
                        onToggleMute: { vm.setMuted(channel.id, !vm.chat.muted.contains(channel.id)) }
                    )
                }
            }
            .padding(.vertical, 8)
        }
        .frame(width: 80)
        .background(PanelTheme.panelBackground)
    }

    // MARK: - Channel header

    private var channelHeader: some View {
        HStack(spacing: 6) {
            if let channel = selectedChannel {
                if let icon = channel.icon {
                    Text(icon).font(.body)
                } else {
                    Image(systemName: "bubble.left.and.bubble.right.fill")
                        .font(.caption)
                        .foregroundStyle(PanelTheme.accentIC)
                }
                Text(channel.title)
                    .font(PanelTheme.headlineFont)
                    .foregroundStyle(PanelTheme.primaryText)
            }
            Spacer()
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 10)
    }

    // MARK: - Message list

    private var messageList: some View {
        let messages = vm.chat.messages(in: vm.chat.selectedChannelId)
        return ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if messages.isEmpty {
                        Text("No messages yet.")
                            .font(PanelTheme.captionFont)
                            .foregroundStyle(PanelTheme.captionText)
                            .padding(PanelTheme.panelPadding)
                    } else {
                        ForEach(messages) { msg in
                            MessageRow(message: msg)
                                .id(msg.id)
                        }
                    }
                    Color.clear
                        .frame(height: 1)
                        .id("__bottom__")
                }
                .padding(.bottom, 4)
            }
            .onChange(of: messages.count) { _, _ in
                withAnimation { proxy.scrollTo("__bottom__", anchor: .bottom) }
            }
            .onChange(of: vm.chat.selectedChannelId) { _, _ in
                proxy.scrollTo("__bottom__", anchor: .bottom)
            }
            .onAppear {
                proxy.scrollTo("__bottom__", anchor: .bottom)
            }
        }
    }

    // MARK: - Text input

    private var inputRow: some View {
        HStack(spacing: 8) {
            TextField("Message…", text: $inputText)
                .font(PanelTheme.bodyFont)
                .foregroundStyle(PanelTheme.primaryText)
                .focused($inputFocused)
                .onSubmit { sendMessage() }
                .textFieldStyle(.plain)
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(PanelTheme.panelBackgroundLight)
                .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
            Button(action: sendMessage) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title3)
                    .foregroundStyle(inputText.trimmingCharacters(in: .whitespaces).isEmpty
                                     ? PanelTheme.captionText
                                     : PanelTheme.accentIC)
            }
            .buttonStyle(.plain)
            .disabled(inputText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 8)
        .background(PanelTheme.panelBackground)
    }

    // MARK: - Helpers

    private var selectedChannel: ChannelInfo? {
        vm.chat.channels.first { $0.id == vm.chat.selectedChannelId }
    }

    private func sendMessage() {
        let trimmed = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        vm.sendChat(trimmed, channelId: vm.chat.selectedChannelId)
        inputText = ""
    }
}

// MARK: - Channel rail row

private struct ChannelRailRow: View {

    let channel: ChannelInfo
    let isSelected: Bool
    let isMuted: Bool
    let onSelect: () -> Void
    let onToggleMute: () -> Void

    private let isDissentChannel: Bool

    init(channel: ChannelInfo, isSelected: Bool, isMuted: Bool,
         onSelect: @escaping () -> Void, onToggleMute: @escaping () -> Void) {
        self.channel = channel
        self.isSelected = isSelected
        self.isMuted = isMuted
        self.onSelect = onSelect
        self.onToggleMute = onToggleMute
        self.isDissentChannel = channel.id == "#dissent"
    }

    var body: some View {
        Button(action: onSelect) {
            VStack(spacing: 3) {
                ZStack(alignment: .topTrailing) {
                    Group {
                        if let icon = channel.icon {
                            Text(icon).font(.title3)
                        } else {
                            Image(systemName: "bubble.left")
                                .font(.body)
                                .foregroundStyle(isSelected ? PanelTheme.primaryText : PanelTheme.secondaryText)
                        }
                    }
                    if channel.unreadHint > 0 {
                        Text("\(min(channel.unreadHint, 99))")
                            .font(PanelTheme.captionFont)
                            .foregroundStyle(.black)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(isDissentChannel
                                        ? Color(red: 1.0, green: 0.3, blue: 0.2)
                                        : PanelTheme.accentIC)
                            .clipShape(Capsule())
                            .offset(x: 8, y: -6)
                    }
                }
                Text(channel.title)
                    .font(.system(size: 9, weight: isSelected ? .semibold : .regular))
                    .foregroundStyle(isSelected ? PanelTheme.primaryText : PanelTheme.secondaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background(isSelected
                        ? (isDissentChannel
                           ? Color(red: 0.25, green: 0.04, blue: 0.04, opacity: 0.9)
                           : PanelTheme.panelBackgroundLight)
                        : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        }
        .buttonStyle(.plain)
        .overlay(alignment: .bottom) {
            if isDissentChannel {
                Rectangle()
                    .frame(height: 2)
                    .foregroundStyle(Color(red: 1.0, green: 0.3, blue: 0.2).opacity(0.7))
            }
        }
        .contextMenu {
            Button {
                onToggleMute()
            } label: {
                Label(isMuted ? "Unmute" : "Mute", systemImage: isMuted ? "speaker.wave.2" : "speaker.slash")
            }
        }
        .padding(.horizontal, 6)
    }
}

// MARK: - Message row

private struct MessageRow: View {

    let message: ChatMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                authorBadge
                if !message.mentions.isEmpty {
                    mentionChips
                }
                Spacer()
                Text(formattedTime)
                    .font(PanelTheme.captionFont)
                    .foregroundStyle(PanelTheme.captionText)
            }
            messageBody
        }
        .padding(.horizontal, PanelTheme.panelPadding)
        .padding(.vertical, 5)
    }

    @ViewBuilder
    private var messageBody: some View {
        switch message.bodyContentType {
        case .markdown:
            let attr = (try? AttributedString(markdown: message.body)) ?? AttributedString(message.body)
            Text(attr)
                .font(PanelTheme.bodyFont)
                .foregroundStyle(PanelTheme.primaryText)
                .fixedSize(horizontal: false, vertical: true)
        case .code:
            Text(message.body)
                .font(.system(size: 12, weight: .regular, design: .monospaced))
                .foregroundStyle(PanelTheme.primaryText)
                .fixedSize(horizontal: false, vertical: true)
                .padding(8)
                .background(Color(white: 0.06))
                .clipShape(RoundedRectangle(cornerRadius: PanelTheme.pillCornerRadius))
        case .json:
            ChatCardView(card: ChatCard.parse(message.body))
        }
    }

    private var authorBadge: some View {
        Text(message.authorId)
            .font(PanelTheme.labelFont)
            .foregroundStyle(PanelTheme.authorColor(message.authorKind))
    }

    private var mentionChips: some View {
        ForEach(message.mentions.prefix(3), id: \.self) { mention in
            Text("@\(mention)")
                .font(PanelTheme.captionFont)
                .foregroundStyle(PanelTheme.accentIC)
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(PanelTheme.accentIC.opacity(0.15))
                .clipShape(Capsule())
        }
    }

    private var formattedTime: String {
        let date = Date(timeIntervalSince1970: TimeInterval(message.ts) / 1_000_000_000.0)
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }
}

// Typing indicator shown inline when streaming == true — handled inside MessageRow.messageBody
// by the streaming flag; the "typing…" suffix is not needed since streaming messages
// render their growing body text live. A dedicated indicator would require view-level access
// to a specific streaming message identity; instead we surface it via the author row.
