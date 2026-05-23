import SwiftUI

struct TapInput: ViewModifier {
    let onTap: (CGPoint) -> Void

    init(onTap: @escaping (CGPoint) -> Void) {
        self.onTap = onTap
    }

    func body(content: Content) -> some View {
        content.gesture(
            SpatialTapGesture()
                .onEnded { value in
                    onTap(value.location)
                }
        )
    }
}

extension View {
    func tapInput(_ onTap: @escaping (CGPoint) -> Void) -> some View {
        modifier(TapInput(onTap: onTap))
    }
}
