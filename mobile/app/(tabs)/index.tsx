import React, { useRef, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Image,
    Linking,
    StyleSheet,
    Text,
    View,
    Pressable,
} from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import { File } from "expo-file-system";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { PlateHighlight } from "@/components/plate-highlight";
import { IconSymbol } from "@/components/ui/icon-symbol";
import { BACKEND_URL } from "@/constants/config";

type Photo = { uri: string; width: number; height: number };

type BestDetection = {
    bbox: number[];
    yolo_conf: number;
    ocr: { text: string; ocr_conf: number } | null;
};

function sleep(ms: number) {
    return new Promise((r) => setTimeout(r, ms));
}

async function fetchJsonWithRetry(
    url: string,
    options: RequestInit,
    {
        retries = 3,
        backoffMs = 1200,
        timeoutMs = 20000,
    }: { retries?: number; backoffMs?: number; timeoutMs?: number } = {}
) {
    let lastErr: any = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const res = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(id);

            // Try parse JSON; if not JSON, treat as text
            const text = await res.text();
            let data: any = null;
            try {
                data = text ? JSON.parse(text) : null;
            } catch {
                data = { raw: text };
            }

            // Handle warming_up from backend
            if (data?.status === "warming_up") {
                if (attempt < retries) {
                    await sleep(backoffMs * (attempt + 1));
                    continue;
                }
                return { ok: false, status: res.status, data };
            }

            // Retry on common transient statuses
            if (!res.ok && [502, 503, 504].includes(res.status) && attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }

            return { ok: res.ok, status: res.status, data };
        } catch (e: any) {
            clearTimeout(id);
            lastErr = e;

            // Retry on network/timeout
            if (attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }
            throw lastErr;
        }
    }

    throw lastErr;
}

export default function HomeScreen() {
    const insets = useSafeAreaInsets();
    const cameraRef = useRef<CameraView>(null);
    const [permission, requestPermission] = useCameraPermissions();
    const [cameraReady, setCameraReady] = useState(false);

    // photo set = the captured frame is shown instead of the live camera.
    // Its pixel size maps the server's plate bbox onto the screen.
    const [photo, setPhoto] = useState<Photo | null>(null);
    const [frame, setFrame] = useState<{ width: number; height: number } | null>(null);
    const [bestDetection, setBestDetection] = useState<BestDetection | null>(null);

    const [loadingDetect, setLoadingDetect] = useState(false);
    const [loadingLog, setLoadingLog] = useState(false);

    // Back to the live camera for the next plate
    const resetToLive = () => {
        setPhoto(null);
        setBestDetection(null);
    };

    // Capture from the in-app camera (no native camera screen)
    const takePhotoAndDetect = async () => {
        if (!cameraRef.current || !cameraReady) return;
        const shot = await cameraRef.current.takePictureAsync({ quality: 0.7, shutterSound: false });
        if (shot?.uri) await detectPhoto({ uri: shot.uri, width: shot.width, height: shot.height });
    };

    // Use a photo already on the phone (and the only way to test in the simulator, which has no camera)
    const pickPhotoAndDetect = async () => {
        const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.7 });
        if (result.canceled) return;
        const asset = result.assets[0];
        await detectPhoto({ uri: asset.uri, width: asset.width, height: asset.height });
    };

    const detectPhoto = async (shot: Photo) => {
        setPhoto(shot);
        setBestDetection(null);
        setLoadingDetect(true);

        try {
            // SDK 58's fetch only accepts Blob-like parts, not { uri, name, type };
            // expo-file-system's File reads the photo and supplies its name and type
            const formData = new FormData();
            formData.append("file", new File(shot.uri));

            const { ok, data } = await fetchJsonWithRetry(
                `${BACKEND_URL}/anpr/detect`,
                { method: "POST", body: formData },
                // CPU inference plus a possible cold start can exceed 25 s; a timeout re-uploads the photo
                { retries: 3, backoffMs: 1200, timeoutMs: 45000 }
            );

            if (!ok) {
                Alert.alert("Server error", data?.message || JSON.stringify(data));
                resetToLive();
                return;
            }

            if (data?.status === "needs_rescan") {
                // Show what the model did see, so a near miss is explainable
                const top: BestDetection | undefined = data?.detections?.[0];
                const seen = top
                    ? `\n\nClosest read: ${top.ocr?.text ?? "(no text)"}\n` +
                      `Detector ${Math.round(top.yolo_conf * 100)}% · ` +
                      `OCR ${top.ocr ? Math.round(top.ocr.ocr_conf * 100) + "%" : "–"}`
                    : "\n\nNo plate found in the photo.";
                Alert.alert("Rescan needed", (data?.message || "Could not confidently read plate.") + seen);
                resetToLive();
                return;
            }

            if (data?.status === "ok" && data?.best) {
                setBestDetection(data.best);
                return;
            }

            Alert.alert("Unexpected response", JSON.stringify(data));
            resetToLive();
        } catch (e: any) {
            Alert.alert("Network error", e?.message ?? String(e));
            resetToLive();
        } finally {
            setLoadingDetect(false);
        }
    };

    const confirmAndLog = async () => {
        if (!bestDetection?.ocr?.text) {
            Alert.alert("Nothing to log", "Scan a plate first.");
            return;
        }

        setLoadingLog(true);

        try {
            const payload = {
                plate_text: bestDetection.ocr.text,
                yolo_conf: bestDetection.yolo_conf,
                ocr_conf: bestDetection.ocr.ocr_conf,
                bbox: bestDetection.bbox,
                source: "mobile_confirm",
            };

            const { ok, data } = await fetchJsonWithRetry(
                `${BACKEND_URL}/anpr/log`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                },
                { retries: 2, backoffMs: 1200, timeoutMs: 20000 }
            );

            if (!ok) {
                Alert.alert("Logging failed", data?.message || JSON.stringify(data));
                return;
            }

            if (data?.status === "duplicate_ignored") {
                Alert.alert("Duplicate ignored", `Already logged: ${data.logged_plate}`);
                resetToLive();
                return;
            }

            if (data?.status === "ok") {
                Alert.alert("Logged ✅", `Plate logged: ${data.logged_plate}`);
                resetToLive();
                return;
            }

            Alert.alert("Unexpected response", JSON.stringify(data));
        } catch (e: any) {
            Alert.alert("Network error", e?.message ?? String(e));
        } finally {
            setLoadingLog(false);
        }
    };

    const ocr = bestDetection?.ocr;
    const confidence = ocr ? confidenceLabel(ocr.ocr_conf) : null;

    return (
        <View style={styles.screen}>
            <View style={[styles.header, { paddingTop: insets.top + 6 }]}>
                <Text style={styles.title}>FleetScan</Text>
            </View>

            {/* Live camera fills most of the screen; after a scan it shows the captured frame */}
            <View
                style={styles.viewfinder}
                onLayout={(e) => setFrame({ width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height })}
            >
                {photo ? (
                    <Image source={{ uri: photo.uri }} style={StyleSheet.absoluteFill} resizeMode="cover" />
                ) : permission?.granted ? (
                    <CameraView
                        ref={cameraRef}
                        style={StyleSheet.absoluteFill}
                        facing="back"
                        onCameraReady={() => setCameraReady(true)}
                    />
                ) : permission ? (
                    <View style={styles.permissionBox}>
                        <IconSymbol name="camera.fill" size={40} color="#fff" />
                        <Text style={styles.permissionText}>Camera access is needed to scan plates.</Text>
                        <Pressable
                            style={styles.permissionBtn}
                            onPress={permission.canAskAgain ? requestPermission : () => Linking.openSettings()}
                        >
                            <Text style={styles.btnText}>{permission.canAskAgain ? "Allow camera" : "Open Settings"}</Text>
                        </Pressable>
                    </View>
                ) : null}

                {/* Plate-shaped guide while aiming */}
                {/* Result: dim everything except the plate the server found */}
                {photo && bestDetection && frame && (
                    <PlateHighlight bbox={bestDetection.bbox} photo={photo} frame={frame} />
                )}

                {!photo && permission?.granted && (
                    <View style={styles.guideLayer} pointerEvents="none">
                        <View style={styles.guideFrame} />
                        <View style={styles.hintPill}>
                            <Text style={styles.hintText}>Fit the plate inside the frame</Text>
                        </View>
                    </View>
                )}

                {loadingDetect && (
                    <View style={styles.readingOverlay}>
                        <ActivityIndicator size="large" color="#fff" />
                        <Text style={styles.readingText}>Reading plate…</Text>
                    </View>
                )}
            </View>

            {/* Result: the plate styled like a NZ plate, with a plain-English confidence */}
            {ocr && confidence && (
                <View style={styles.resultCard}>
                    {/* Black plastic surround, white plate, thin black inset border */}
                    <View style={styles.plateSurround}>
                        <View style={styles.plate}>
                            <View style={styles.plateInset}>
                                <Text style={styles.plateText}>{ocr.text}</Text>
                            </View>
                        </View>
                    </View>
                    <View style={styles.confidenceRow}>
                        <View style={[styles.dot, { backgroundColor: confidence.color }]} />
                        <Text style={styles.confidenceText}>{confidence.label}</Text>
                    </View>
                </View>
            )}

            {/* Actions pinned to the bottom, in thumb reach, above the home indicator */}
            <View style={[styles.bottomBar, { paddingBottom: Math.max(insets.bottom, 16) }]}>
                {ocr ? (
                    <View style={styles.actionRow}>
                        <Pressable style={[styles.secondaryBtn, styles.flex1]} onPress={resetToLive} disabled={loadingLog}>
                            <Text style={styles.secondaryText}>Rescan</Text>
                        </Pressable>
                        <Pressable
                            style={({ pressed }) => [styles.confirmBtn, styles.flex2, (pressed || loadingLog) && { opacity: 0.8 }]}
                            onPress={confirmAndLog}
                            disabled={loadingLog}
                        >
                            {loadingLog ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Confirm & Log</Text>}
                        </Pressable>
                    </View>
                ) : (
                    <View style={styles.actionRow}>
                        <Pressable
                            style={({ pressed }) => [styles.iconBtn, (pressed || loadingDetect) && { opacity: 0.6 }]}
                            onPress={pickPhotoAndDetect}
                            disabled={loadingDetect}
                            accessibilityLabel="Choose a photo"
                        >
                            <IconSymbol name="photo.on.rectangle" size={24} color="#fff" />
                        </Pressable>
                        <Pressable
                            style={({ pressed }) => [
                                styles.primaryBtn,
                                styles.flex1,
                                (pressed || loadingDetect || !cameraReady) && { opacity: 0.6 },
                            ]}
                            onPress={takePhotoAndDetect}
                            disabled={loadingDetect || !cameraReady || !!photo}
                        >
                            <View style={styles.btnRow}>
                                <IconSymbol name="camera.fill" size={22} color="#fff" />
                                <Text style={styles.btnText}>{loadingDetect ? "Reading…" : "Scan Plate"}</Text>
                            </View>
                        </Pressable>
                    </View>
                )}
            </View>
        </View>
    );
}

// Staff-facing wording for the OCR confidence
function confidenceLabel(ocrConf: number) {
    if (ocrConf >= 0.8) return { label: "High confidence", color: "#22c55e" };
    if (ocrConf >= 0.6) return { label: "Medium confidence: check the plate", color: "#f59e0b" };
    return { label: "Low confidence: check carefully", color: "#ef4444" };
}

// Fills the parent (React Native 0.88 dropped StyleSheet.absoluteFillObject)
const fill = { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 } as const;

const styles = StyleSheet.create({
    screen: { flex: 1, backgroundColor: "#0b0b0c" },
    header: { paddingHorizontal: 18, paddingBottom: 10 },
    title: { color: "#fff", fontSize: 20, fontWeight: "800", textAlign: "center" },

    viewfinder: { flex: 1, marginHorizontal: 12, borderRadius: 22, overflow: "hidden", backgroundColor: "#1c1c1e" },
    guideLayer: { ...fill, alignItems: "center", justifyContent: "center" },
    // NZ plates are about 3:1
    guideFrame: { width: "82%", aspectRatio: 3, borderWidth: 3, borderColor: "rgba(255,255,255,0.9)", borderRadius: 12 },
    hintPill: {
        position: "absolute",
        bottom: 20,
        backgroundColor: "rgba(0,0,0,0.55)",
        paddingHorizontal: 14,
        paddingVertical: 8,
        borderRadius: 999,
    },
    hintText: { color: "#fff", fontSize: 14, fontWeight: "600" },
    readingOverlay: {
        ...fill,
        backgroundColor: "rgba(0,0,0,0.45)",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
    },
    readingText: { color: "#fff", fontSize: 16, fontWeight: "700" },
    permissionBox: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 14 },
    permissionText: { color: "#fff", fontSize: 16, textAlign: "center" },
    permissionBtn: { backgroundColor: "#2e7dff", paddingVertical: 12, paddingHorizontal: 22, borderRadius: 12 },

    resultCard: { marginHorizontal: 12, marginTop: 12, alignItems: "center", gap: 10 },
    plateSurround: {
        backgroundColor: "#0b0b0b",
        borderRadius: 14,
        borderWidth: 1,
        borderColor: "#3a3a3c", // keeps the black surround visible on the dark screen
        padding: 7,
        paddingBottom: 16, // surrounds are deeper below the plate
    },
    plate: { backgroundColor: "#fff", borderRadius: 7, padding: 4 },
    plateInset: {
        borderWidth: 2,
        borderColor: "#111",
        borderRadius: 5,
        paddingVertical: 4,
        paddingHorizontal: 20,
    },
    plateText: { fontSize: 40, fontWeight: "900", letterSpacing: 6, color: "#111" },
    confidenceRow: { flexDirection: "row", alignItems: "center", gap: 8 },
    dot: { width: 10, height: 10, borderRadius: 5 },
    confidenceText: { color: "#e5e5e5", fontSize: 14, fontWeight: "600" },

    bottomBar: { paddingHorizontal: 12, paddingTop: 12 },
    actionRow: { flexDirection: "row", gap: 10 },
    flex1: { flex: 1 },
    flex2: { flex: 2 },
    btnRow: { flexDirection: "row", alignItems: "center", gap: 10 },

    iconBtn: { backgroundColor: "#2c2c2e", width: 56, borderRadius: 14, alignItems: "center", justifyContent: "center" },
    primaryBtn: { backgroundColor: "#2e7dff", paddingVertical: 16, borderRadius: 14, alignItems: "center" },
    confirmBtn: { backgroundColor: "#16a34a", paddingVertical: 16, borderRadius: 14, alignItems: "center" },
    secondaryBtn: { backgroundColor: "#2c2c2e", paddingVertical: 16, borderRadius: 14, alignItems: "center" },
    btnText: { color: "#fff", fontSize: 17, fontWeight: "700" },
    secondaryText: { color: "#fff", fontSize: 17, fontWeight: "700" },
});
