import React, { useState } from "react";
import { ActivityIndicator, Alert, Linking, Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { Palette, TAP, font, radius, space, useColors } from "@/constants/brand";
import { BACKEND_URL, apiHeaders } from "@/constants/config";
import { fetchJsonWithRetry } from "@/lib/fetch-json";

// GET /sheets/current
type SheetStatus = {
    mode: "sheets" | "console";
    name: string | null; // the spreadsheet in use, null when there's none yet
    url: string | null;
    tab: string; // today's tab, e.g. 17/11/2026
    month_title: string; // what a new month's file would be called
    can_start_month: boolean;
};

type Props = {
    visible: boolean;
    onClose: () => void;
    onMonthStarted: () => void; // send any scans that were waiting for a sheet
};

async function call(path: string, method: "GET" | "POST") {
    try {
        const { status, data } = await fetchJsonWithRetry(
            `${BACKEND_URL}${path}`,
            { method, headers: apiHeaders() },
            { retries: 1, backoffMs: 1000, timeoutMs: 15000 }
        );
        if (status === 401) return { status: "error", message: "This app's key isn't accepted by the server." };
        return data ?? { status: "error", message: "The server didn't answer." };
    } catch {
        return { status: "error", message: "No signal, or the server didn't answer." };
    }
}

// Where scans are going, and the switch to this month's spreadsheet
export function SheetSettings({ visible, onClose, onMonthStarted }: Props) {
    const color = useColors();
    const styles = makeStyles(color);
    const [sheet, setSheet] = useState<SheetStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [starting, setStarting] = useState(false);

    const load = async () => {
        setError(null);
        const data = await call("/sheets/current", "GET");
        if (data?.status === "ok") setSheet(data);
        else setError(data?.message ?? "Couldn't load the sheet.");
    };

    const startMonth = async () => {
        setStarting(true);
        const data = await call("/sheets/new-month", "POST");
        setStarting(false);
        if (data?.status !== "ok") {
            Alert.alert("Not started", data?.message ?? "Couldn't start the new sheet.");
            return;
        }
        onMonthStarted();
        await load();
    };

    const confirmStart = () => {
        if (!sheet) return;
        Alert.alert(
            `Start ${sheet.month_title}?`,
            sheet.name
                ? `Scans from now on go to a new spreadsheet, copied from the template. ${sheet.name} is kept.`
                : "Scans from now on go to a new spreadsheet, copied from the template.",
            [
                { text: "Cancel", style: "cancel" },
                { text: "Start", onPress: startMonth },
            ]
        );
    };

    const thisMonthInUse = sheet?.name === sheet?.month_title;

    return (
        <Modal
            visible={visible}
            animationType="slide"
            presentationStyle="pageSheet"
            onShow={load} // fresh each time it opens
            onRequestClose={onClose}
        >
            <View style={styles.sheet}>
                <View style={styles.header}>
                    <Text style={styles.title} accessibilityRole="header">
                        Audit sheet
                    </Text>
                    <Pressable onPress={onClose} style={styles.done} accessibilityRole="button">
                        <Text style={styles.doneText}>Done</Text>
                    </Pressable>
                </View>

                {!sheet && !error && <ActivityIndicator color={color.accent} style={styles.loading} />}

                {error && (
                    <View accessibilityLiveRegion="polite">
                        <Text style={styles.warning}>{error}</Text>
                        <Pressable onPress={load} style={styles.link} accessibilityRole="button">
                            <Text style={styles.linkText}>Try again</Text>
                        </Pressable>
                    </View>
                )}

                {sheet?.mode === "console" && (
                    <Text style={styles.body}>
                        The server isn&apos;t connected to Google Sheets, so scans are only written to its log.
                    </Text>
                )}

                {sheet?.mode === "sheets" && (
                    <>
                        <Text style={styles.label}>Scans go to</Text>
                        {sheet.name ? (
                            <>
                                <Text style={styles.value}>{sheet.name}</Text>
                                <Text style={styles.meta}>{`Today's tab: ${sheet.tab}`}</Text>
                                {sheet.url && (
                                    <Pressable
                                        onPress={() => Linking.openURL(sheet.url!)}
                                        style={styles.link}
                                        accessibilityRole="link"
                                    >
                                        <Text style={styles.linkText}>Open in Google Sheets</Text>
                                    </Pressable>
                                )}
                            </>
                        ) : (
                            <Text style={[styles.value, styles.warningText]}>
                                No spreadsheet yet. Start this month&apos;s to begin logging.
                            </Text>
                        )}

                        <View style={styles.divider} />

                        {sheet.can_start_month ? (
                            <>
                                <Pressable
                                    onPress={confirmStart}
                                    disabled={starting || thisMonthInUse}
                                    style={[styles.button, (starting || thisMonthInUse) && styles.buttonOff]}
                                    accessibilityRole="button"
                                    accessibilityState={{ disabled: starting || thisMonthInUse }}
                                >
                                    {starting ? (
                                        <ActivityIndicator color={color.onFill} />
                                    ) : (
                                        <Text style={styles.buttonText} maxFontSizeMultiplier={1.4}>
                                            {`Start ${sheet.month_title}`}
                                        </Text>
                                    )}
                                </Pressable>
                                <Text style={styles.meta}>
                                    {thisMonthInUse
                                        ? "This month's spreadsheet is already in use."
                                        : "Copies the template into a new spreadsheet. Do this once at the start of each month."}
                                </Text>
                            </>
                        ) : (
                            <Text style={styles.meta}>
                                New months aren&apos;t set up on the server (no template or folder).
                            </Text>
                        )}
                    </>
                )}
            </View>
        </Modal>
    );
}

const makeStyles = (color: Palette) =>
    StyleSheet.create({
        sheet: { flex: 1, backgroundColor: color.bg, paddingHorizontal: space.md },
        header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: space.md },
        title: { ...font.title, fontSize: 22, color: color.text },
        done: { minHeight: TAP, justifyContent: "center", paddingHorizontal: space.sm },
        doneText: { ...font.label, color: color.accent },
        loading: { marginTop: space.xl },
        label: { ...font.caption, color: color.textMuted, marginTop: space.sm },
        value: { ...font.label, color: color.text, marginTop: space.xs },
        body: { ...font.body, color: color.text },
        meta: { ...font.caption, color: color.textMuted, marginTop: space.sm },
        warning: { ...font.body, color: color.warning },
        warningText: { color: color.warning },
        link: { minHeight: TAP, justifyContent: "center", alignSelf: "flex-start" },
        linkText: { ...font.label, fontSize: 16, color: color.accent },
        divider: { height: StyleSheet.hairlineWidth, backgroundColor: color.divider, marginVertical: space.md },
        button: {
            minHeight: 56,
            borderRadius: radius.lg,
            backgroundColor: color.accent,
            alignItems: "center",
            justifyContent: "center",
            paddingHorizontal: space.md,
        },
        buttonOff: { opacity: 0.45 },
        buttonText: { ...font.label, color: color.onFill },
    });
