import AppKit
import CoreGraphics
import Foundation

struct FloatingState {
    var title: String = "无设备"
    var subtitle: String = "等待连接"
    var footer: String = "右键刷新"
    var state: String = "empty"
    var topmostLocked: Bool = true
    var topmostLabel: String = "置顶"
}

final class FloatingView: NSView {
    var state = FloatingState() {
        didSet {
            needsDisplay = true
        }
    }

    var onRestore: (() -> Void)?
    var onAction: ((String) -> Void)?
    var onMove: ((CGFloat, CGFloat) -> Void)?
    private var dragStartScreen: NSPoint?
    private var windowStart: NSPoint?
    private(set) var isDragging = false
    private var didDrag = false

    override var isFlipped: Bool {
        true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let bounds = self.bounds
        let cardRect = bounds.insetBy(dx: 4, dy: 4)
        let shadowRect = cardRect.offsetBy(dx: 2, dy: 3)

        context.saveGState()
        context.setShadow(offset: CGSize(width: 0, height: -2), blur: 10, color: NSColor(calibratedWhite: 0, alpha: 0.18).cgColor)
        let shadowPath = NSBezierPath(roundedRect: shadowRect, xRadius: 14, yRadius: 14)
        NSColor(calibratedWhite: 0, alpha: 0.08).setFill()
        shadowPath.fill()
        context.restoreGState()

        let colors = palette()
        let cardPath = NSBezierPath(roundedRect: cardRect, xRadius: 14, yRadius: 14)
        colors.background.setFill()
        cardPath.fill()
        colors.border.setStroke()
        cardPath.lineWidth = 1
        cardPath.stroke()

        let dotRect = NSRect(x: 20, y: bounds.midY - 7, width: 14, height: 14)
        colors.dot.setFill()
        NSBezierPath(ovalIn: dotRect).fill()

        drawText(state.title, in: NSRect(x: 48, y: 18, width: bounds.width - 126, height: 18), color: colors.title, size: 13, weight: .semibold)
        drawText(state.subtitle, in: NSRect(x: 48, y: 41, width: bounds.width - 134, height: 17), color: colors.subtitle, size: 11, weight: .regular)
        drawText(state.footer, in: NSRect(x: bounds.width - 98, y: 41, width: 82, height: 17), color: colors.subtitle, size: 11, weight: .regular, alignment: .right)
        drawText("右键菜单", in: NSRect(x: bounds.width - 88, y: bounds.height - 25, width: 72, height: 16), color: NSColor(calibratedRed: 0.58, green: 0.65, blue: 0.74, alpha: 1), size: 10, weight: .regular, alignment: .right)

        let badgeRect = NSRect(x: bounds.width - 66, y: 15, width: 50, height: 19)
        let badgePath = NSBezierPath(roundedRect: badgeRect, xRadius: 9, yRadius: 9)
        (state.topmostLocked ? NSColor(calibratedRed: 0.94, green: 0.97, blue: 1.0, alpha: 1) : NSColor(calibratedRed: 0.95, green: 0.97, blue: 0.99, alpha: 1)).setFill()
        badgePath.fill()
        drawText(
            state.topmostLabel,
            in: badgeRect.offsetBy(dx: 0, dy: 2),
            color: state.topmostLocked ? NSColor(calibratedRed: 0.15, green: 0.39, blue: 0.92, alpha: 1) : NSColor(calibratedRed: 0.39, green: 0.45, blue: 0.55, alpha: 1),
            size: 10,
            weight: .semibold,
            alignment: .center
        )
    }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount >= 2 {
            onRestore?()
            return
        }
        dragStartScreen = NSEvent.mouseLocation
        windowStart = window?.frame.origin
        isDragging = true
        didDrag = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window, let dragStartScreen, let windowStart else { return }
        let current = NSEvent.mouseLocation
        let next = NSPoint(
            x: windowStart.x + current.x - dragStartScreen.x,
            y: windowStart.y + current.y - dragStartScreen.y
        )
        window.setFrameOrigin(next)
        didDrag = true
    }

    override func mouseUp(with event: NSEvent) {
        defer {
            dragStartScreen = nil
            windowStart = nil
            isDragging = false
            didDrag = false
        }
        guard didDrag, let frame = window?.frame else { return }
        onMove?(frame.origin.x, NSScreen.mainScreenHeight - frame.origin.y - frame.height)
    }

    override func rightMouseDown(with event: NSEvent) {
        let menu = NSMenu()
        add(menu, "打开主界面", "restore")
        add(menu, state.topmostLocked ? "取消置顶锁定" : "置顶锁定", "toggle_topmost")
        menu.addItem(NSMenuItem.separator())
        add(menu, "刷新设备", "refresh_devices")
        add(menu, "设备详情", "device_info")
        add(menu, "锁定当前设备", "lock_device")
        add(menu, "解除锁定", "unlock_device")
        menu.addItem(NSMenuItem.separator())
        add(menu, "获取前台包名", "foreground_package")
        add(menu, "刷新运行进程", "refresh_processes")
        menu.addItem(NSMenuItem.separator())
        add(menu, "读取日志", "dump_logcat")
        add(menu, "保存日志", "save_logcat")
        add(menu, "清空日志", "clear_logcat")
        menu.addItem(NSMenuItem.separator())
        add(menu, "截屏", "screencap")
        add(menu, "录屏", "screenrecord")
        menu.addItem(NSMenuItem.separator())
        add(menu, "退出程序", "quit_app")
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }

    @objc private func menuAction(_ sender: NSMenuItem) {
        guard let action = sender.representedObject as? String else { return }
        onAction?(action)
    }

    private func add(_ menu: NSMenu, _ title: String, _ action: String) {
        let item = NSMenuItem(title: title, action: #selector(menuAction(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = action
        menu.addItem(item)
    }

    private func drawText(
        _ text: String,
        in rect: NSRect,
        color: NSColor,
        size: CGFloat,
        weight: NSFont.Weight,
        alignment: NSTextAlignment = .left
    ) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = alignment
        paragraph.lineBreakMode = .byTruncatingTail
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: color,
            .paragraphStyle: paragraph,
        ]
        (text as NSString).draw(in: rect, withAttributes: attrs)
    }

    private func palette() -> (background: NSColor, border: NSColor, dot: NSColor, title: NSColor, subtitle: NSColor) {
        switch state.state {
        case "warning":
            return (
                NSColor(calibratedRed: 1.0, green: 0.95, blue: 0.95, alpha: 1),
                NSColor(calibratedRed: 0.98, green: 0.65, blue: 0.65, alpha: 1),
                NSColor(calibratedRed: 0.94, green: 0.27, blue: 0.27, alpha: 1),
                NSColor(calibratedRed: 0.60, green: 0.11, blue: 0.11, alpha: 1),
                NSColor(calibratedRed: 0.50, green: 0.11, blue: 0.11, alpha: 1)
            )
        case "connected":
            return (
                NSColor(calibratedRed: 0.97, green: 0.98, blue: 0.99, alpha: 1),
                NSColor(calibratedRed: 0.84, green: 0.87, blue: 0.91, alpha: 1),
                NSColor(calibratedRed: 0.13, green: 0.77, blue: 0.37, alpha: 1),
                NSColor(calibratedRed: 0.07, green: 0.09, blue: 0.15, alpha: 1),
                NSColor(calibratedRed: 0.29, green: 0.35, blue: 0.42, alpha: 1)
            )
        default:
            return (
                NSColor(calibratedRed: 0.97, green: 0.98, blue: 0.99, alpha: 1),
                NSColor(calibratedRed: 0.80, green: 0.84, blue: 0.88, alpha: 1),
                NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 1),
                NSColor(calibratedRed: 0.20, green: 0.25, blue: 0.33, alpha: 1),
                NSColor(calibratedRed: 0.39, green: 0.45, blue: 0.55, alpha: 1)
            )
        }
    }
}

final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class FloatingController {
    private let width: CGFloat = 300
    private let height: CGFloat = 88
    private let lockedLevel = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.screenSaverWindow)) + 1)
    private let panel: FloatingPanel
    private let content: FloatingView

    init() {
        panel = FloatingPanel(
            contentRect: NSRect(x: 80, y: NSScreen.mainScreenHeight - 80 - 88, width: width, height: height),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        content = FloatingView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        panel.contentView = content
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.ignoresMouseEvents = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.level = lockedLevel

        content.onRestore = { emit(["event": "restore"]) }
        content.onAction = { action in emit(["event": "action", "action": action]) }
        content.onMove = { x, y in emit(["event": "moved", "x": Int(x), "y": Int(y)]) }
    }

    func apply(_ message: [String: Any]) {
        if !content.isDragging, let x = message["x"] as? Int, let y = message["y"] as? Int {
            panel.setFrameOrigin(NSPoint(x: CGFloat(x), y: NSScreen.mainScreenHeight - CGFloat(y) - height))
        }
        let topmost = message["topmostLocked"] as? Bool ?? true
        panel.level = topmost ? lockedLevel : .normal
        panel.collectionBehavior = topmost ? [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle] : [.fullScreenAuxiliary]
        content.state = FloatingState(
            title: message["title"] as? String ?? "无设备",
            subtitle: message["subtitle"] as? String ?? "等待连接",
            footer: message["footer"] as? String ?? "右键刷新",
            state: message["state"] as? String ?? "empty",
            topmostLocked: topmost,
            topmostLabel: message["topmostLabel"] as? String ?? (topmost ? "置顶" : "普通")
        )
        panel.orderFrontRegardless()
    }

    func close() {
        panel.orderOut(nil)
        NSApp.terminate(nil)
    }
}

extension NSScreen {
    static var mainScreenHeight: CGFloat {
        NSScreen.screens.map { $0.frame.maxY }.max() ?? NSScreen.main?.frame.height ?? 0
    }
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: []),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
    fflush(stdout)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = FloatingController()
controller.apply([:])

Thread.detachNewThread {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data, options: []),
              let message = object as? [String: Any],
              let type = message["type"] as? String else {
            continue
        }
        DispatchQueue.main.async {
            if type == "quit" {
                controller.close()
            } else if type == "update" {
                controller.apply(message)
            }
        }
    }
    DispatchQueue.main.async {
        controller.close()
    }
}

app.run()
