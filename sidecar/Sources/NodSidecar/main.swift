import FlyingFox
import Foundation
import TTSKit
import WhisperKit

// Nod sidecar: ears (WhisperKit), voice (TTSKit), hands (Accessibility API).
// Contract lives in ../SPEC.md. Localhost only.

setvbuf(stdout, nil, _IOLBF, 0)
let port: UInt16 = 8790

func json(_ object: Any, status: HTTPStatusCode = .ok) -> HTTPResponse {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    return HTTPResponse(statusCode: status, headers: [.contentType: "application/json"], body: data)
}

let server = HTTPServer(address: try .inet(ip4: "127.0.0.1", port: port))

await server.appendRoute("GET /health") { _ in
    json(["ok": true])
}

print("[sidecar] listening on http://127.0.0.1:\(port)")
try await server.run()
