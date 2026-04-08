import React, { useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Image,
    ScrollView,
    StyleSheet,
    Text,
    View,
    Pressable,
} from "react-native";
import * as ImagePicker from "expo-image-picker";

const BACKEND_URL = "https://anpr-production-c38d.up.railway.app";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchJsonWithRetry(url, options, { retries = 3, backoffMs = 1200, timeoutMs = 20000 } = {}) {
    let lastErr = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const res = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(id);

            const text = await res.text();
            let data = null;
            try {
                data = text ? JSON.parse(text) : null;
            } catch {
                data = { raw: text };
            }

            if (data?.status === "warming_up") {
                if (attempt < retries) {
                    await sleep(backoffMs * (attempt + 1));
                    continue;
                }
                return { ok: false, status: res.status, data };
            }

            if (!res.ok && [502, 503, 504].includes(res.status) && attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }

            return { ok: res.ok, status: res.status, data };
        } catch (e) {
            clearTimeout(id);
            lastErr = e;
            if (attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }
            throw lastErr;
        }
    }

    throw lastErr;
}

export default function App() {
    const [imageUri, setImageUri] = useState(null);
    const [bestDetection, setBestDetection] = useState(null);
    const [loadingDetect, setLoadingDetect] = useState(false);
    const [loadingLog, setLoadingLog] = useState(false);

    const takePhotoAndDetect = async () => {
        const { status } = await ImagePicker.requestCameraPermissionsAsync();
        if (status !== "granted") {
            Alert.alert("Permission required", "Camera access is needed.");
            return;
        }

        const result = await ImagePicker.launchCameraAsync({ quality: 0.7 });
        if (result.canceled) return;

        const asset = result.assets[0];
        setImageUri(asset.uri);
        setBestDetection(null);
        setLoadingDetect(true);

        try {
            const formData = new FormData();
            formData.append("file", { uri: asset.uri, name: "plate.jpg", type: "image/jpeg" });

            const { ok, data } = await fetchJsonWithRetry(
                `${BACKEND_URL}/anpr/detect`,
                { method: "POST", body: formData },
                { retries: 3, backoffMs: 1200, timeoutMs: 25000 }
            );

            if (!ok) {
                Alert.alert("Server error", data?.message || JSON.stringify(data));
                return;
            }

            if (data?.status === "needs_rescan") {
                Alert.alert("Rescan needed", data?.message || "Could not confidently read plate.");
                return;
            }

            if (data?.status === "ok" && data?.best) {
                setBestDetection(data.best);
                return;
            }

            Alert.alert("Unexpected response", JSON.stringify(data));
        } catch (err) {
            Alert.alert("Network error", err?.message ?? String(err));
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
                setBestDetection(null);
                return;
            }

            if (data?.status === "ok") {
                Alert.alert("Logged ✅", `Plate logged: ${data.logged_plate}`);
                setBestDetection(null);
                return;
            }

            Alert.alert("Unexpected response", JSON.stringify(data));
        } catch (err) {
            Alert.alert("Network error", err?.message ?? String(err));
        } finally {
            setLoadingLog(false);
        }
    };

    return (
        <ScrollView contentContainerStyle={styles.container}>
            <Text style={styles.title}>ANPR Scanner</Text>

            <Pressable style={styles.primaryBtn} onPress={takePhotoAndDetect} disabled={loadingDetect}>
                {loadingDetect ? <ActivityIndicator /> : <Text style={styles.btnText}>📷 Scan Plate</Text>}
            </Pressable>

            {imageUri && (
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Captured Image</Text>
                    <Image source={{ uri: imageUri }} style={styles.image} />
                </View>
            )}

            {bestDetection?.ocr?.text && (
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Detected Plate</Text>
                    <Text style={styles.plateText}>{bestDetection.ocr.text}</Text>
                    <Text style={styles.muted}>
                        YOLO: {Number(bestDetection.yolo_conf).toFixed(2)} • OCR: {Number(bestDetection.ocr.ocr_conf).toFixed(2)}
                    </Text>

                    <Pressable style={[styles.confirmBtn, loadingLog && { opacity: 0.6 }]} onPress={confirmAndLog} disabled={loadingLog}>
                        {loadingLog ? <ActivityIndicator /> : <Text style={styles.btnText}>✅ Confirm & Log</Text>}
                    </Pressable>

                    <Pressable style={styles.secondaryBtn} onPress={() => setBestDetection(null)} disabled={loadingLog}>
                        <Text style={styles.secondaryText}>Cancel</Text>
                    </Pressable>
                </View>
            )}
        </ScrollView>
    );
}

const styles = StyleSheet.create({
    container: { flexGrow: 1, paddingTop: 60, paddingHorizontal: 18, backgroundColor: "#f5f5f5" },
    title: { fontSize: 24, fontWeight: "800", textAlign: "center", marginBottom: 18 },
    section: { marginTop: 16 },
    sectionTitle: { fontSize: 16, fontWeight: "700", marginBottom: 8 },
    image: { width: "100%", height: 240, borderRadius: 10 },
    plateText: { fontSize: 32, fontWeight: "900", letterSpacing: 4, marginTop: 8 },
    muted: { marginTop: 6, color: "#555" },

    primaryBtn: { backgroundColor: "#2e7dff", paddingVertical: 14, borderRadius: 12, alignItems: "center" },
    confirmBtn: { backgroundColor: "#0bbf5e", paddingVertical: 14, borderRadius: 12, alignItems: "center", marginTop: 14 },
    secondaryBtn: { paddingVertical: 12, borderRadius: 12, alignItems: "center", marginTop: 10, backgroundColor: "#e9e9e9" },
    btnText: { color: "#fff", fontSize: 16, fontWeight: "700" },
    secondaryText: { color: "#222", fontSize: 15, fontWeight: "700" },
});