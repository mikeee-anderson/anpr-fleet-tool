import Constants from "expo-constants";

/**
 * Backend URL, in order of preference:
 * 1. EXPO_PUBLIC_API_URL (from mobile/.env, e.g. the Cloud Run URL)
 * 2. In development, the machine running Expo on port 8000, so Expo Go on
 *    a phone on the same Wi-Fi reaches a local `docker compose up` backend
 * 3. http://localhost:8000 (simulator / web)
 */
function devMachineUrl(): string | null {
    // hostUri looks like "192.168.1.20:8081" when the bundle is served by Metro
    const hostUri = Constants.expoConfig?.hostUri;
    if (!hostUri) return null;
    return `http://${hostUri.split(":")[0]}:8000`;
}

export const BACKEND_URL = (
    process.env.EXPO_PUBLIC_API_URL || devMachineUrl() || "http://localhost:8000"
).replace(/\/+$/, "");

/**
 * Key for the endpoints that write to the Sheet (EXPO_PUBLIC_API_KEY, the
 * server's API_KEY). It ships inside the app, so it keeps out strangers who
 * find the URL, not someone who unpacks the app.
 */
export const API_KEY = process.env.EXPO_PUBLIC_API_KEY ?? "";

export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
    return API_KEY ? { ...extra, "X-API-Key": API_KEY } : extra;
}
