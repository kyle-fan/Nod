import Foundation

/// Fixes names Whisper mis-spells ("Kayden Ron" -> "Caden Ruan") by matching what was heard
/// against names we know are real: the people in hub/profile.json and the chats in the
/// Messages sidebar. The hub and Bonsai then see the contact's actual name.
final class NameBook: @unchecked Sendable {
    static let shared = NameBook()
    private let lock = NSLock()
    private var fixed: [String] = []       // from the profile
    private var contacts: [String] = []    // from Messages
    private var entries: [(words: [String], keys: [String])] = []

    func setProfileNames(_ names: [String]) { lock.withLock { fixed = names; rebuild() } }
    func setContacts(_ names: [String]) { lock.withLock { contacts = names; rebuild() } }
    var count: Int { lock.withLock { entries.count } }

    private func rebuild() {
        var seen = Set<String>()
        var out: [(words: [String], keys: [String])] = []
        func add(_ words: [String]) {
            let id = words.joined(separator: " ").lowercased()
            guard !seen.contains(id) else { return }
            seen.insert(id)
            out.append((words, words.map(NameBook.sound)))
        }
        for name in fixed + contacts {
            let words = name.split(separator: " ").map(String.init)
            // Real person-style names only: 1 to 3 words, letters only, capitalised.
            guard (1...3).contains(words.count),
                  words.allSatisfy({ w in w.count >= 3 && w.allSatisfy { $0.isLetter && $0.isASCII } && w.first!.isUppercase })
            else { continue }
            add(words)
            if words.count > 1 { add([words[0]]) }   // "Caden Ruan" is also just "Caden"
        }
        // Longest names first, so "Caden Ruan" wins over "Caden".
        entries = out.sorted { $0.words.count > $1.words.count }
    }

    /// Rough sound of a word: "kayden" and "caden" both become "kdn".
    static func sound(_ word: String) -> String {
        var s = word.lowercased().filter { $0.isLetter }
        for (a, b) in [("ph", "f"), ("ck", "k"), ("gh", ""), ("wr", "r"), ("kn", "n"), ("th", "t")] {
            s = s.replacingOccurrences(of: a, with: b)
        }
        var out = ""
        let chars = Array(s)
        for (i, ch) in chars.enumerated() {
            var c = ch
            switch ch {
            case "c":
                let next = i + 1 < chars.count ? chars[i + 1] : " "
                c = "eiy".contains(next) ? "s" : "k"
            case "q": c = "k"
            case "z": c = "s"
            case "v": c = "f"
            case "j": c = "g"
            case "a", "e", "i", "o", "u", "y", "h", "w":
                if i == 0 { c = "a" } else { continue }   // vowels only matter at the start
            default: break
            }
            if out.last != c { out.append(c) }
        }
        return out
    }

    private static func similarity(_ a: String, _ b: String) -> Double {
        let x = Array(a.lowercased()), y = Array(b.lowercased())
        if x.isEmpty || y.isEmpty { return 0 }
        var row = Array(0...y.count)
        for i in 1...x.count {
            var prev = row[0]
            row[0] = i
            for j in 1...y.count {
                let cur = row[j]
                row[j] = min(row[j] + 1, row[j - 1] + 1, prev + (x[i - 1] == y[j - 1] ? 0 : 1))
                prev = cur
            }
        }
        return 1 - Double(row[y.count]) / Double(max(x.count, y.count))
    }

    /// Replace heard words that sound like a known name with that name.
    func correct(_ text: String) -> String {
        let entries = lock.withLock { self.entries }
        guard !entries.isEmpty, !text.isEmpty else { return text }
        var tokens = text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)

        // Split "Ron," into ("Ron", ",") so punctuation survives the swap.
        func core(_ t: String) -> (String, String) {
            let trailing = String(t.reversed().prefix { !$0.isLetter }.reversed())
            return (String(t.dropLast(trailing.count)), trailing)
        }

        var i = 0
        while i < tokens.count {
            var replaced = false
            for entry in entries where i + entry.words.count <= tokens.count {
                let slice = (0..<entry.words.count).map { core(tokens[i + $0]) }
                let heard = slice.map(\.0)
                // Inner words must not carry punctuation ("Ron, Kayden" is not one name).
                guard heard.allSatisfy({ $0.count >= 3 && $0.allSatisfy(\.isLetter) }),
                      slice.dropLast().allSatisfy({ $0.1.isEmpty }),
                      heard.allSatisfy({ $0.first!.isUppercase }) else { continue }
                if heard.map({ $0.lowercased() }) == entry.words.map({ $0.lowercased() }) { replaced = true; i += entry.words.count; break }
                guard zip(heard, entry.keys).allSatisfy({ NameBook.sound($0) == $1 }) else { continue }
                let score = NameBook.similarity(heard.joined(), entry.words.joined())
                // A single word needs to be closer, and not the first word of a sentence
                // (those are capitalised anyway: "Alone" must not become "Alan").
                if entry.words.count == 1 {
                    let sentenceStart = i == 0 || ".?!".contains(tokens[i - 1].last ?? " ")
                    guard score >= 0.6, !sentenceStart else { continue }
                } else {
                    guard score >= 0.45 else { continue }
                }
                for (k, word) in entry.words.enumerated() { tokens[i + k] = word + slice[k].1 }
                i += entry.words.count
                replaced = true
                break
            }
            if !replaced { i += 1 }
        }
        return tokens.joined(separator: " ")
    }
}
