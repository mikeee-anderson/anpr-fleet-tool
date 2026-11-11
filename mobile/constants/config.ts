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
