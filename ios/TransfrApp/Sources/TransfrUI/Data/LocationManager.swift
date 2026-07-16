import Foundation
import CoreLocation
import Observation

/// Bridges CoreLocation into the app's observable world. One-shot and **while-in-use
/// only** (design/route-maps.html §3): we want the user's coordinate to plan *from*
/// and to place the live "you" dot — never background tracking. The manager is
/// created on the MainActor, so its delegate callbacks arrive on the main thread;
/// each one `assumeIsolated`s back onto the actor to publish state that views
/// observe directly.
@MainActor
@Observable
public final class LocationManager: NSObject {
    /// The latest fix, or nil until one arrives (or if denied).
    public private(set) var coordinate: CLLocationCoordinate2D?
    public private(set) var authorization: CLAuthorizationStatus = .notDetermined
    /// True from the moment we ask until a fix or failure — drives the field spinner.
    public private(set) var isRequesting = false

    @ObservationIgnored private let manager = CLLocationManager()

    public override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        authorization = manager.authorizationStatus
    }

    /// Ask for permission if needed and pull a single fresh fix. Safe to call
    /// repeatedly — both the first-launch default and the button route through here.
    public func request() {
        isRequesting = true
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()      // the fix follows in the delegate
        case .authorizedWhenInUse, .authorizedAlways:
            manager.requestLocation()
        default:
            isRequesting = false                          // denied / restricted — give up quietly
        }
    }

    public var isDenied: Bool { authorization == .denied || authorization == .restricted }
}

extension LocationManager: CLLocationManagerDelegate {
    // Callbacks arrive on the main thread (the manager was created on the MainActor),
    // so each reads the Sendable values it needs, then `assumeIsolated`s to publish.
    // `self.manager` is used inside — never the non-Sendable parameter — so nothing
    // gets sent across an isolation boundary.
    public nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        MainActor.assumeIsolated {
            self.authorization = status
            switch status {
            case .authorizedWhenInUse, .authorizedAlways:
                if self.isRequesting { self.manager.requestLocation() }
            case .denied, .restricted:
                self.isRequesting = false
            default:
                break
            }
        }
    }

    public nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let coord = locations.last?.coordinate
        MainActor.assumeIsolated {
            if let coord { self.coordinate = coord }
            self.isRequesting = false
        }
    }

    public nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        MainActor.assumeIsolated { self.isRequesting = false }
    }
}
