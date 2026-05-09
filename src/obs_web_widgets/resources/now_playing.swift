import CoreFoundation
import Darwin
import Foundation

let framework = "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote"

func printJSON(_ payload: [String: Any]) {
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        print("{\"ok\":false,\"error\":\"json serialization failed\"}")
    }
}

guard let handle = dlopen(framework, RTLD_NOW) else {
    printJSON(["ok": false, "error": "failed to load MediaRemote"])
    exit(1)
}

var appBundleID = ""

if let appSymbol = dlsym(handle, "MRMediaRemoteGetNowPlayingApplicationDisplayID") {
    typealias GetApp = @convention(c) (DispatchQueue, @escaping (CFString?) -> Void) -> Void
    let getApp = unsafeBitCast(appSymbol, to: GetApp.self)
    let appSemaphore = DispatchSemaphore(value: 0)

    getApp(DispatchQueue.global(qos: .userInitiated)) { app in
        appBundleID = app as String? ?? ""
        appSemaphore.signal()
    }

    _ = appSemaphore.wait(timeout: .now() + 1)
}

guard let infoSymbol = dlsym(handle, "MRMediaRemoteGetNowPlayingInfo") else {
    printJSON(["ok": false, "error": "MRMediaRemoteGetNowPlayingInfo is unavailable"])
    exit(1)
}

typealias GetInfo = @convention(c) (DispatchQueue, @escaping (CFDictionary?) -> Void) -> Void
let getInfo = unsafeBitCast(infoSymbol, to: GetInfo.self)
let infoSemaphore = DispatchSemaphore(value: 0)
var output: [String: Any] = [
    "ok": false,
    "appBundleID": appBundleID,
]

getInfo(DispatchQueue.global(qos: .userInitiated)) { dictionary in
    var payload: [String: Any] = [
        "ok": true,
        "appBundleID": appBundleID,
        "capturedAt": Date().timeIntervalSince1970,
    ]

    guard let info = dictionary as? [String: Any] else {
        output = payload
        infoSemaphore.signal()
        return
    }

    func stringValue(_ key: String) -> String {
        return info[key] as? String ?? ""
    }

    func doubleValue(_ key: String) -> Double? {
        if let value = info[key] as? Double {
            return value
        }
        if let value = info[key] as? NSNumber {
            return value.doubleValue
        }
        return nil
    }

    payload["title"] = stringValue("kMRMediaRemoteNowPlayingInfoTitle")
    payload["artist"] = stringValue("kMRMediaRemoteNowPlayingInfoArtist")
    payload["album"] = stringValue("kMRMediaRemoteNowPlayingInfoAlbum")
    payload["duration"] = doubleValue("kMRMediaRemoteNowPlayingInfoDuration") ?? 0
    payload["elapsed"] = doubleValue("kMRMediaRemoteNowPlayingInfoElapsedTime") ?? 0
    payload["playbackRate"] = doubleValue("kMRMediaRemoteNowPlayingInfoPlaybackRate") ?? 0

    if let timestamp = info["kMRMediaRemoteNowPlayingInfoTimestamp"] as? Date {
        payload["timestamp"] = timestamp.timeIntervalSince1970
    } else {
        payload["timestamp"] = Date().timeIntervalSince1970
    }

    output = payload
    infoSemaphore.signal()
}

if infoSemaphore.wait(timeout: .now() + 2) != .success {
    output = [
        "ok": false,
        "appBundleID": appBundleID,
        "error": "timed out reading Now Playing",
    ]
}

printJSON(output)
