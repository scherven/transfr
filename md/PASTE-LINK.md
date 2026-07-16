# Paste link: turning a Maps / rail link into an itinerary

The trip-input screen (`InputView.swift`) has a **Paste link** door whose UI
existed but did nothing — "nothing turns a Google/Apple Maps / DB Navigator link
into an itinerary" (TODO.md §1). This note surveys what each of the three link
families actually carries, what's recoverable, what's structurally blocked, and
exactly what shipped vs. what was deferred.

The parser is **pure and unit-tested** in
`ios/TransfrCore/Sources/TransfrCore/RouteLinkParser.swift`
(tests: `Tests/TransfrCoreTests/RouteLinkParserTests.swift`, 16 cases, all green).
The one runtime concern — expanding a short link over HTTP — is isolated in the
app layer (`ios/TransfrApp/Sources/TransfrUI/Data/LinkExpander.swift`), and the
whole thing converges on the existing `/journeys` plan path via
`TripModel.planFromLink(_:)`.

---

## The core constraint: `/journeys` takes station **names**

Everything downstream flows from one fact. The app plans by sending station
**names** to `GET /journeys?from=&to=` (see `TripModel.plan()` /
`LiveRepository`). There is no client-side name→stop-id resolver — the
name→stop-id normalisation gap of TODO.md §9 is unchanged by this work. So a
link is "plannable" exactly when we can pull a **name** for each end. A link that
only carries coordinates (a dropped pin) needs a reverse step before it can plan
(see "Coordinate fallback" below).

The parser therefore produces:

```
ParsedRouteLink { from: String?, to: String?,
                  fromCoordinate: Coordinate?, toCoordinate: Coordinate?,
                  departure: Date?, travelMode: TravelMode, source: Source }
```

`isPlannable` is true only when both `from` and `to` are non-blank names.

---

## 1. Google Maps — the link in the field today

The app's default paste is `https://maps.app.goo.gl/JWTvpehbneTcqad39`. That is a
**short link**: a bare `302` redirect. Following it (verified by `curl -L`)
yields the real URL, which is where all the content lives:

```
https://www.google.com/maps/dir/
  Brussel-Zuid,+Av.+Fonsny+47B,+1060+Bruxelles,+Belgium/
  München+Hauptbahnhof,+Bayerstraße+10A,+80335+München,+Germany/
  @49.6523411,5.3147974,757692m/am=t/
  data=…!1d4.3370087!2d50.8364402…!1d11.5582682!2d48.1404108!3e3!5i5?entry=tts
```

**Recoverable:**

| datum | where | example |
|---|---|---|
| origin name/address | 1st `/dir/` path segment | `Brussel-Zuid, Av. Fonsny 47B, 1060 Bruxelles, Belgium` |
| destination name/address | last `/dir/` path segment | `München Hauptbahnhof, Bayerstraße 10A, …` |
| endpoint coordinates | `data=` block `!1d<lng>!2d<lat>` pairs, in order | origin `(50.836, 4.337)`, dest `(48.140, 11.558)` |
| travel mode | `data=` `!3e<n>` (`0`drive·`1`cycle·`2`walk·`3`transit·`4`flight), or `?travelmode=`, or `am=t` | `!3e3` → transit |

The parser reduces each address to its **first comma-separated component** —
`"München Hauptbahnhof, Bayerstraße 10A, …"` → `"München Hauptbahnhof"` — which
for a shared *station* is the station itself, and is exactly the shape
`/journeys` autocompletes against. The `@lat,lng,zoom` in the path is only the
**map viewport**, never an endpoint, so it is deliberately ignored for coords;
the real endpoint coords come from the `data` block.

Also handled: the Maps-URLs API form
`…/maps/dir/?api=1&origin=…&destination=…&travelmode=transit`; an empty first
segment (`/dir//Dest`) which Google uses for "your location" (→ `from == nil`); and
the **classic directions form** that the Maps app's share sheet actually emits
today — `maps.google.com/?saddr=<from>&daddr=<to>&dirflg=<mode>` (endpoints in the
query, mode letter `r`/`w`/`d`/`b`, possibly suffixed as in `dirflg=rBSTR`). A
short link like `maps.app.goo.gl/ug6h1EyzTy6diDvx6` redirects to this shape, not to
`/maps/dir/`, so the parser reads `saddr`/`daddr` names as well as the path form.

**Blocked / not recoverable:**
- **Departure time.** A chosen "depart at" is buried in the opaque protobuf-ish
  `data` block with no stable, documented layout. Not parsed. (Most shared
  Google links carry no departure anyway.)
- **`place_id:` origins/destinations** (the API form allows them) are opaque ids
  we can't turn into a name; they'd fall through as un-resolvable.
- **Platform / track.** Never present in any maps link — that's what the transfr
  engine computes server-side; a maps link has no concept of it.

**Short-link expansion:** `RouteLinkParser.parse` throws
`.shortLinkNeedsExpansion(url)` for a `goo.gl` host; the app follows the redirect
with `URLSession` (which follows `302`s natively) and re-parses the result. The
expander **stops at the first non-short redirect target** so it captures the
`/maps/dir/…` `Location` without downloading the multi-hundred-KB Maps HTML.

---

## 2. Apple Maps — `maps.apple.com/?…`

Apple's documented URL scheme (Apple Developer, *Map Links*) is
query-param-based:

| param | meaning |
|---|---|
| `saddr` | source (address, name, or `lat,lng`) |
| `daddr` | destination (only `daddr` is required) |
| `dirflg` | `d` driving · `w` walking · `r` transit |
| `q` / `name` / `address` | a place label (place links) |
| `ll` / `sll` / `coordinate` | a `lat,lng` centre/point |

**Recoverable:** origin/destination names (`saddr`/`daddr`), or a single place
name (`q`/`name`/`address`) for a shared pin; the transit flag (`dirflg=r`); and
`lat,lng` when a value is a coordinate rather than a name. A place link (single
`q`+`ll`) yields a **destination only** (`from == nil`, not plannable on its own).

**Blocked:** no departure time in the scheme; no platform data. A `daddr` that is
a bare coordinate gives a point but no name (handled via the coordinate fallback).

**Verified vs. documented:** unlike the Google case, I had no live Apple share
link to expand, so the Apple fixtures are built from the **documented** scheme
(and the classic `saddr/daddr/dirflg` form is stable and widely used). The
newer "unified" `maps.apple.com/place?…` links reuse the same `address`/`q`/`ll`
keys, which the parser already accepts.

---

## 3. Deutsche Bahn / bahn.de

The new site puts the query in the URL **fragment** (after `#`); the legacy
`reiseauskunft.bahn.de` used the query string.

New (`https://www.bahn.de/buchung/fahrplan/suche#…`):

| param | meaning |
|---|---|
| `so` / `zo` | origin / destination **name** (e.g. `Hamburg Hbf`) |
| `soid` / `zoid` | structured stop id: `A=1@O=<name>@X=…@L=<EVA>@…` |
| `hd` | departure datetime, `2026-07-15T08:34:00` (local) |

Legacy (`…/bin/query.exe/dn?…`): `S` / `Z` names, `date` (`dd.MM.yy`) + `time`.

**Recoverable — the richest of the three:** both **names** (`so`/`zo`, or the
`@O=` inside `soid`/`zoid` as a fallback), a real **departure time** (`hd`, or
`date`+`time`), and — uniquely — the **EVA stop ids** (`@L=…`) embedded in
`soid`/`zoid`. Travel mode is always transit.

**Implemented:** names + departure. `hd` is parsed with a fixed formatter (the
year width is read from the digit count so a 2-digit `.yy` isn't misread as year
0026 — see the test `parsesOldReiseauskunft`).

**Deferred (and why):** the **EVA ids are extracted-in-principle but not used**,
because `/journeys` has no id-based query — feeding an EVA would require the §9
name→id normalisation to run the *other* direction (id→queryable) server-side.
When §9 lands, a bahn.de link is the one family that could plan by exact stop id
with zero ambiguity; today we deliberately pass the plain name and let the same
autocomplete matching handle it.

---

## What shipped

- **`RouteLinkParser`** (pure, `TransfrCore`) — Google (short + full `/dir/` +
  API form), Apple (`saddr/daddr/dirflg` + place), bahn.de (new fragment + legacy
  query). Returns `{from, to, fromCoordinate, toCoordinate, departure, travelMode}`
  and throws precise `ParseError`s (`.notAURL`, `.unrecognizedProvider`,
  `.noEndpoints`, `.shortLinkNeedsExpansion`).
- **`URLSessionLinkExpander`** (app layer) — follows the short-link `302`, stops
  before the destination page. Behind a `LinkExpanding` protocol so it's
  injectable/stubbable.
- **`TripModel.planFromLink(_:)`** — expand → parse → (coordinate fallback) →
  set `origin/destination/departure` → `plan()`. Fails soft: every error path
  sets `load = .failed(message)`, surfaced on the CTA exactly like type-mode.
- **`InputView` CTA** now routes `Mode.paste` to `planFromLink(link)`; the button
  disables on an empty link and spins during expand+plan. The formerly-dead
  **Recent** rows now plan their example route on tap.

### Coordinate fallback (name-less pins)

When an end has a coordinate but no name (a dropped pin), `planFromLink`
reverse-resolves it via `repo.platforms(lat:lon:)` — the **same**
`/station-platforms` nearest-station lookup that "Use my current location" already
uses — turning the point into a queryable station name. This reuses existing
infra rather than adding a geocoder, and only fires when the name is genuinely
absent (the common Google case already has names in the path).

## What's deferred / structurally blocked

- **Departure from Google/Apple links** — not in a stable/parseable place (Google
  protobuf) or not in the scheme at all (Apple). bahn.de departure *is* parsed.
- **Platform / track** — never present in any of these links by nature; it is a
  server-side computation, not a shareable field.
- **EVA / place-id → plannable query** — gated on the §9 name→stop-id
  normalisation. Until then we plan by name for every family, so the paste path
  inherits the same name-matching behaviour (and limits) as typed input.
- **Live redirect-following is not unit-tested** (it's a network hop). Per the
  task, the *post-expansion* parser is tested directly against the real expanded
  `/maps/dir/…` string that `maps.app.goo.gl/JWTvpehbneTcqad39` redirects to
  (`parsesRealExpandedGoogleDir`); the expander itself is a thin, injectable seam.
```
