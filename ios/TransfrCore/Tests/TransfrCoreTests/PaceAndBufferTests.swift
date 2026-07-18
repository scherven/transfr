import Foundation
import Testing
@testable import TransfrCore

/// Issue #36 — the walking-pace and boarding-buffer settings actually affect
/// routing. Two pure, offline concerns live in this module:
///   * `Transfer.pacedWalkTimeS(_:)` — walking pace SCALES the shown walk time
///     (display-only; the server verdict is untouched).
///   * `AssessRequest.bufferS` — the boarding buffer rides the `/assess` wire so
///     the streamed verdicts match the `/journeys` search's buffer.
struct PaceAndBufferTests {

    private func transfer(walk: Double?) -> Transfer {
        Transfer(atStation: "Somewhere", relationId: 1,
                 arrivalPlatform: "3", departurePlatform: "7",
                 layoverS: 300, walkTimeS: walk, walkDistanceM: 120,
                 verdict: "feasible")
    }

    // MARK: - Walking pace scales the displayed walk time

    @Test func normalPaceIsIdentity() {
        // factor 1 == the routing engine's assumed pace: the number is unchanged.
        #expect(transfer(walk: 90).pacedWalkTimeS(1) == 90)
    }

    @Test func relaxedPaceLengthensBriskShortens() {
        let t = transfer(walk: 100)
        let relaxed = try! #require(t.pacedWalkTimeS(1.15))
        let brisk = try! #require(t.pacedWalkTimeS(0.85))
        #expect(abs(relaxed - 115) < 1e-9)   // slower walker -> longer shown walk
        #expect(abs(brisk - 85) < 1e-9)      // faster walker -> shorter shown walk
        // Ordering holds for any base: relaxed > normal > brisk.
        let normal = try! #require(t.pacedWalkTimeS(1))
        #expect(relaxed > normal)
        #expect(brisk < normal)
    }

    @Test func unknownWalkStaysNil() {
        // A pending / unresolved transfer has no walk time; scaling keeps it nil so
        // callers still fall through to "—" — pace never invents a number.
        #expect(transfer(walk: nil).pacedWalkTimeS(1.15) == nil)
    }

    @Test func paceDoesNotTouchTheVerdict() {
        // The guarantee behind #36's deferral: scaling the walk must not re-verdict.
        let t = transfer(walk: 90)
        _ = t.pacedWalkTimeS(1.15)
        #expect(t.verdict == "feasible")
        #expect(t.verdictKind == .feasible)
    }

    // MARK: - Boarding buffer rides the /assess wire

    private func encodedKeys(_ req: AssessRequest) throws -> [String: Any] {
        let data = try TransfrJSON.encoder.encode(req)
        return try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    @Test func bufferEncodesAsSnakeCaseWhenSet() throws {
        let obj = try encodedKeys(AssessRequest(interchanges: [], noElevators: false, bufferS: 90))
        #expect(obj["buffer_s"] as? Int == 90)          // snake_case, matches api/schemas.py
        #expect(obj["no_elevators"] as? Bool == false)  // the sibling field still rides
    }

    @Test func bufferOmittedWhenNil() throws {
        // nil -> the key is absent, so the server uses its own default and the
        // request is byte-identical to before this field existed.
        let obj = try encodedKeys(AssessRequest(interchanges: [], noElevators: true, bufferS: nil))
        #expect(obj["buffer_s"] == nil)
        #expect(obj["no_elevators"] as? Bool == true)
    }
}
