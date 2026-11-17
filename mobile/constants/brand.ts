// FleetScan design tokens. See design.md: every screen uses these, not raw values.

import { useColorScheme } from "react-native";

// One neutral (navy), one accent (glacier blue), one success colour.
// Warning amber is only for "check this" states.
const dark = {
    bg: "#010F2C", // screen background: the darkest navy in the logo sheet
    surface: "#0C1B48", // sheets and empty camera area
    accent: "#62A7EA", // the one accent: primary action, links
    success: "#22C55E", // the one success colour: plate locked, log button
    warning: "#F59E0B", // something to check; never blocks
    text: "#FFFFFF",
    textMuted: "rgba(255,255,255,0.7)",
    onFill: "#010F2C", // text and icons on accent or success fills
    border: "rgba(255,255,255,0.35)", // outlined buttons
    divider: "rgba(255,255,255,0.15)",
    panel: "rgba(1,15,44,0.9)", // the bottom panel over the camera
    // the plate card mimics a real NZ plate in both themes
    plate: "#FFFFFF",
    plateInk: "#111111",
    plateSurround: "#0B0B0B",
};

// Daylight: dark UIs wash out in direct sun. Same roles, darker accent and
// warning so text on white still passes contrast.
const light: Palette = {
    bg: "#F4F7FB",
    surface: "#FFFFFF",
    accent: "#1F6FC5",
    success: "#16A34A",
    warning: "#B45309",
    text: "#0B1530",
    textMuted: "rgba(11,21,48,0.65)",
    onFill: "#FFFFFF",
    border: "rgba(11,21,48,0.3)",
    divider: "rgba(11,21,48,0.12)",
    panel: "rgba(244,247,251,0.95)",
    plate: "#FFFFFF",
    plateInk: "#111111",
    plateSurround: "#0B0B0B",
};

export type Palette = Record<keyof typeof dark, string>;

// Follows the phone's light/dark setting
export function useColors(): Palette {
    return useColorScheme() === "light" ? light : dark;
}

// Anything drawn on the camera feed sits on a dark, dimmed image in both
// themes, so it keeps the dark-theme look
export const camera = {
    text: "#FFFFFF",
    textMuted: "rgba(255,255,255,0.65)",
    accent: dark.accent,
    success: dark.success,
    dim: "rgba(1,15,44,0.6)",
    empty: dark.surface, // behind the camera before it starts
} as const;

// 8-point grid, with 4 for tight gaps
export const space = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;

export const radius = { sm: 8, md: 12, lg: 16 } as const;

// iOS system font (SF) throughout; only sizes and weights vary
export const font = {
    caption: { fontSize: 13, fontWeight: "500" },
    body: { fontSize: 15, fontWeight: "500" },
    label: { fontSize: 17, fontWeight: "700" }, // buttons and hints
    title: { fontSize: 20, fontWeight: "700" },
    plate: { fontSize: 40, fontWeight: "900" },
} as const;

export const TAP = 44; // minimum tap target (Apple HIG)
