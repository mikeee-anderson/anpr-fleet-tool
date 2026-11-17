import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { Palette, font, useColors } from "@/constants/brand";

// Characters OCR most often swaps on NZ plates, and what each is mistaken for
export const LOOKALIKES: Record<string, string[]> = {
    O: ["0"],
    "0": ["O"],
    I: ["1"],
    L: ["1"],
    "1": ["I", "L"],
    B: ["8"],
    "8": ["B"],
    S: ["5"],
    "5": ["S"],
    Z: ["2"],
    "2": ["Z"],
};

// Standard NZ formats: ABC123 (since 2001) and AB1234 (older)
export const STANDARD_PLATE = /^(?:[A-Z]{3}[0-9]{3}|[A-Z]{2}[0-9]{4})$/;

/**
 * Positions to double-check. Any character the server changed when
 * correcting the format (rawText is what OCR saw) is flagged. On a standard
 * plate the format already settles letter vs digit, so look-alikes are only
 * flagged on other plates (personalised, or misread into a non-standard shape).
 */
export function flaggedPositions(text: string, rawText?: string) {
    const standard = STANDARD_PLATE.test(text);
    const flagged = new Set<number>();
    [...text].forEach((c, i) => {
        if ((rawText && rawText[i] !== c) || (!standard && LOOKALIKES[c])) flagged.add(i);
    });
    return flagged;
}

type Props = {
    text: string;
    flagged: Set<number>;
    selected: number | null;
    onPressChar?: (index: number) => void;
};

// The reading styled like an NZ plate: black surround, white plate, thin
// inset border. Each character can be tapped to correct it.
export function PlateCard({ text, flagged, selected, onPressChar }: Props) {
    const color = useColors();
    const styles = makeStyles(color);
    return (
        <View style={styles.surround}>
            <View style={styles.plate}>
                <View style={styles.inset}>
                    {[...text].map((c, i) => (
                        <Pressable
                            key={i}
                            onPress={() => onPressChar?.(i)}
                            disabled={!onPressChar}
                            style={[styles.charBox, selected === i && styles.charSelected]}
                            accessibilityRole="button"
                            accessibilityLabel={`Character ${i + 1}, ${c}${flagged.has(i) ? ", check this one" : ""}`}
                            hitSlop={{ top: 8, bottom: 8 }}
                        >
                            {/* scales with Dynamic Type, capped so 8 characters still fit the plate */}
                            <Text style={styles.char} maxFontSizeMultiplier={1.2}>
                                {c}
                            </Text>
                            <View style={[styles.underline, flagged.has(i) && styles.flagged]} />
                        </Pressable>
                    ))}
                </View>
            </View>
        </View>
    );
}

const makeStyles = (color: Palette) =>
    StyleSheet.create({
        surround: {
            backgroundColor: color.plateSurround,
            borderRadius: 14,
            borderWidth: 1,
            borderColor: color.divider, // keeps the black surround visible on the navy screen
            padding: 7,
            paddingBottom: 16, // surrounds are deeper below the plate
        },
        plate: { backgroundColor: color.plate, borderRadius: 7, padding: 4 },
        inset: {
            flexDirection: "row",
            borderWidth: 2,
            borderColor: color.plateInk,
            borderRadius: 5,
            paddingVertical: 2,
            paddingHorizontal: 14,
        },
        charBox: { alignItems: "center", paddingHorizontal: 3, borderRadius: 4 },
        charSelected: { backgroundColor: "rgba(98,167,234,0.3)" }, // accent, faint
        char: { ...font.plate, color: color.plateInk },
        // reserves the space either way, so flagging a character doesn't shift the plate
        underline: { height: 4, alignSelf: "stretch", borderRadius: 2, marginTop: -2, marginBottom: 2 },
        flagged: { backgroundColor: color.warning },
    });

