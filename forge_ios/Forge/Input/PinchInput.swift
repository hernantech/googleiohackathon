import SwiftUI

struct PinchInput: ViewModifier {
    let onScale: (Float) -> Void

    init(onScale: @escaping (Float) -> Void) {
        self.onScale = onScale
    }

    func body(content: Content) -> some View {
        content.gesture(
            MagnificationGesture()
                .onChanged { value in
                    // value is the cumulative scale; report as delta from 1.0
                    onScale(Float(value) - 1.0)
                }
        )
    }
}

extension View {
    func pinchInput(_ onScale: @escaping (Float) -> Void) -> some View {
        modifier(PinchInput(onScale: onScale))
    }
}
