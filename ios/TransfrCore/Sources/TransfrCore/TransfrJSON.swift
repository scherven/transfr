import Foundation

/// The one configured coder pair for every contract in this module. Both the
/// `schemas.py` responses and the `viz_export` JSON use snake_case keys, so a
/// single `.convertFromSnakeCase` strategy lets the Swift structs stay camelCase
/// without hand-written CodingKeys. Use these everywhere rather than a bare
/// `JSONDecoder()` so the whole app stays consistent with the wire format.
public enum TransfrJSON {
    public static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    public static var encoder: JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }

    public static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try decoder.decode(type, from: data)
    }
}
