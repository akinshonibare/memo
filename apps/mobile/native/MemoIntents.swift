// Siri and Shortcuts for memo (ADR 0028). Added to the main app target by
// plugins/withAppIntents.js: App Shortcuts must live in the app bundle for Siri to
// discover them without the user building a Shortcut first.
//
// Both intents open memo and hand over through its own memo:// scheme, which the React
// Native side already routes (App.tsx). The dictated text travels on-device only: it
// is a URL between this process and itself, never a network request.

import AppIntents
import UIKit

struct TakeNoteIntent: AppIntent {
  static let title: LocalizedStringResource = "Take a note"
  static let description = IntentDescription("Save a note to memo. You will be asked what it says.")
  static let openAppWhenRun: Bool = true

  @Parameter(title: "Note", requestValueDialog: "What's the note?")
  var text: String

  @MainActor
  func perform() async throws -> some IntentResult {
    var components = URLComponents()
    components.scheme = "memo"
    components.host = "capture"
    components.queryItems = [URLQueryItem(name: "text", value: text)]
    if let url = components.url {
      await UIApplication.shared.open(url)
    }
    return .result()
  }
}

struct RecordNoteIntent: AppIntent {
  static let title: LocalizedStringResource = "Record a voice note"
  static let description = IntentDescription("Open memo and start recording.")
  static let openAppWhenRun: Bool = true

  @MainActor
  func perform() async throws -> some IntentResult {
    if let url = URL(string: "memo://record") {
      await UIApplication.shared.open(url)
    }
    return .result()
  }
}

struct MemoShortcuts: AppShortcutsProvider {
  static var appShortcuts: [AppShortcut] {
    AppShortcut(
      intent: TakeNoteIntent(),
      phrases: [
        "Take a note in \(.applicationName)",
        "Add a note to \(.applicationName)",
        "Tell \(.applicationName)",
      ],
      shortTitle: "Take a note",
      systemImageName: "square.and.pencil"
    )
    AppShortcut(
      intent: RecordNoteIntent(),
      phrases: [
        "Record in \(.applicationName)",
        "Start a voice note in \(.applicationName)",
      ],
      shortTitle: "Record",
      systemImageName: "mic"
    )
  }
}
