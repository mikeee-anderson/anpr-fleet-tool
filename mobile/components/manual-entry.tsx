import React, { useState } from "react";
import { KeyboardAvoidingView, Modal, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Palette, font, radius, space, useColors } from "@/constants/brand";

// Plates are letters and digits only; anything else typed is dropped
const toPlate = (text: string) => text.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);

type Props = {
    visible: boolean;
    initial?: string; // the current reading, when correcting it
    onCancel: () => void;
    onSubmit: (plate: string) => void;
};

// Typed fallback for dirty, bent or unusual plates the scanner can't read
export function ManualEntry({ visible, initial = "", onCancel, onSubmit }: Props) {
    const color = useColors();
    const styles = makeStyles(color);
    const [text, setText] = useState(initial);

    const close = (plate?: string) => {
        setText("");
        if (plate) onSubmit(plate);
        else onCancel();
    };

    return (
        <Modal visible={visible} transparent animationType="slide" onRequestClose={() => close()}>
            <KeyboardAvoidingView style={styles.backdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <View style={styles.sheet}>
                    <Text style={styles.title}>{initial ? "Fix the plate" : "Type the plate"}</Text>
                    <TextInput
                        style={styles.input}
                        maxFontSizeMultiplier={1.2}
                        value={text}
                        onChangeText={(t) => setText(toPlate(t))}
                        autoFocus
                        autoCapitalize="characters"
                        autoCorrect={false}
                        spellCheck={false}
                        autoComplete="off"
                        returnKeyType="done"
                        placeholder="ABC123"
                        placeholderTextColor="rgba(17,17,17,0.25)"
                        // read the field itself: typing then Return quickly can beat the state update
                        onSubmitEditing={(e) => {
                            const plate = toPlate(e.nativeEvent.text);
                            if (plate) close(plate);
                        }}
                        accessibilityLabel="Plate number"
                    />
                    <View style={styles.row}>
                        <Pressable style={[styles.btn, styles.cancel]} onPress={() => close()}>
                            <Text style={styles.cancelText}>Cancel</Text>
                        </Pressable>
                        <Pressable
                            style={[styles.btn, styles.use, !text && styles.disabled]}
                            onPress={() => close(text)}
                            disabled={!text}
                        >
                            <Text style={styles.useText}>Use plate</Text>
                        </Pressable>
                    </View>
                </View>
            </KeyboardAvoidingView>
        </Modal>
    );
}

const makeStyles = (color: Palette) =>
    StyleSheet.create({
        backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(1,15,44,0.6)" },
        sheet: {
            backgroundColor: color.surface,
            padding: space.md,
            paddingBottom: space.xl,
            borderTopLeftRadius: radius.lg,
            borderTopRightRadius: radius.lg,
            gap: space.md,
        },
        title: { ...font.title, color: color.text },
        input: {
            ...font.plate,
            fontSize: 34,
            backgroundColor: color.plate,
            color: color.plateInk,
            borderRadius: radius.sm,
            paddingVertical: space.md,
            letterSpacing: 6,
            textAlign: "center",
        },
        row: { flexDirection: "row", gap: space.sm },
        btn: { flex: 1, minHeight: 56, borderRadius: radius.lg, alignItems: "center", justifyContent: "center" },
        cancel: { borderWidth: 1.5, borderColor: color.border },
        cancelText: { ...font.label, color: color.text },
        use: { backgroundColor: color.accent },
        useText: { ...font.label, fontWeight: "800", color: color.onFill },
        disabled: { opacity: 0.4 },
    });
