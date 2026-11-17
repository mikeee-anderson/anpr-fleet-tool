import React from "react";
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { Palette, TAP, font, space, useColors } from "@/constants/brand";
import { formatTime, LogEntry } from "@/hooks/use-scan-log";

const STATUS_TEXT: Record<LogEntry["status"], string> = {
    sent: "Logged",
    waiting: "Waiting for signal",
    undone: "Undone",
    rejected: "Not logged",
};

type Props = {
    visible: boolean;
    entries: LogEntry[];
    onUndo: (id: string) => void;
    onClose: () => void;
};

// Today's logs from this phone: check what was logged, see what's still
// waiting to send, and undo a mistake
export function TodayLog({ visible, entries, onUndo, onClose }: Props) {
    const color = useColors();
    const styles = makeStyles(color);
    return (
        <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
            <View style={styles.sheet}>
                <View style={styles.header}>
                    <Text style={styles.title}>Today</Text>
                    <Pressable onPress={onClose} style={styles.done} accessibilityRole="button">
                        <Text style={styles.doneText}>Done</Text>
                    </Pressable>
                </View>
                <FlatList
                    data={entries}
                    keyExtractor={(e) => e.id}
                    ListEmptyComponent={<Text style={styles.empty}>Nothing logged yet today.</Text>}
                    renderItem={({ item }) => {
                        const gone = item.status === "undone" || item.status === "rejected";
                        return (
                            <View style={styles.row}>
                                <View style={styles.flex1}>
                                    <Text style={[styles.plate, gone && styles.struck]}>{item.plate}</Text>
                                    <Text style={[styles.meta, item.status === "waiting" && styles.waiting]}>
                                        {`${formatTime(item.at)} · ${STATUS_TEXT[item.status]}`}
                                    </Text>
                                </View>
                                {!gone && (
                                    <Pressable
                                        onPress={() => onUndo(item.id)}
                                        style={styles.undo}
                                        accessibilityRole="button"
                                        accessibilityLabel={`Undo ${item.plate}`}
                                    >
                                        <Text style={styles.undoText}>Undo</Text>
                                    </Pressable>
                                )}
                            </View>
                        );
                    }}
                />
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
        empty: { ...font.body, color: color.textMuted, paddingVertical: space.lg },
        row: {
            flexDirection: "row",
            alignItems: "center",
            paddingVertical: space.md,
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: color.divider,
        },
        flex1: { flex: 1 },
        plate: { fontSize: 22, fontWeight: "800", letterSpacing: 2, color: color.text },
        struck: { textDecorationLine: "line-through", color: color.textMuted },
        meta: { ...font.caption, color: color.textMuted, marginTop: space.xs },
        waiting: { color: color.warning },
        undo: { minHeight: TAP, minWidth: 64, alignItems: "center", justifyContent: "center" },
        undoText: { ...font.label, fontSize: 16, color: color.accent },
    });
