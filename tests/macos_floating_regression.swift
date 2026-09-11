let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = FloatingController()
let panel = app.windows.compactMap { $0 as? FloatingPanel }.first!
precondition(!panel.isVisible, "Do not show a locked window before reading the saved setting")

controller.apply(["topmostLocked": false])
precondition(panel.isVisible && panel.level == .normal)

let coveringWindow = NSWindow(
    contentRect: panel.frame,
    styleMask: [.borderless],
    backing: .buffered,
    defer: false
)
coveringWindow.isReleasedWhenClosed = false

func assertRefreshStaysBehind() {
    coveringWindow.orderFrontRegardless()
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
    let tracked = Set([panel.windowNumber, coveringWindow.windowNumber])
    func order() -> [Int] {
        // NSApplication.orderedWindows excludes nonactivating panels.
        let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
        return windows.compactMap { $0[kCGWindowNumber as String] as? Int }.filter { tracked.contains($0) }
    }
    let before = order()
    precondition(before == [coveringWindow.windowNumber, panel.windowNumber], "Cover must start in front: \(before), expected \([coveringWindow.windowNumber, panel.windowNumber])")
    for _ in 0..<5 {
        controller.apply(["topmostLocked": false, "title": "设备刷新"])
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
        precondition(order() == before, "An unlocked refresh raised the floating window")
        precondition(panel.level == .normal && panel.isVisible)
    }
}

assertRefreshStaysBehind()
controller.apply(["topmostLocked": true])
precondition(panel.level.rawValue > NSWindow.Level.normal.rawValue)
let lockedLevel = panel.level
controller.apply(["topmostLocked": true, "title": "已连接"])
precondition(panel.level == lockedLevel)
controller.apply(["topmostLocked": false])
precondition(panel.level == .normal)
assertRefreshStaysBehind()

coveringWindow.close()
panel.orderOut(nil)
print("PASS: normal refresh preserves window order before and after toggling topmost")
