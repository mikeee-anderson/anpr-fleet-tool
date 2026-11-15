import React, { useEffect, useState } from "react";
import { Animated, Easing, StyleSheet, View } from "react-native";

type Size = { width: number; height: number };

/**
 * Where a box in photo pixels lands on screen when the photo is drawn with
 * resizeMode="cover" in a frame: scaled to fill, centred, and cropped.
 */
export function coverRect(bbox: number[], photo: Size, frame: Size) {
    const scale = Math.max(frame.width / photo.width, frame.height / photo.height);
    const offsetX = (frame.width - photo.width * scale) / 2;
    const offsetY = (frame.height - photo.height * scale) / 2;
    const [x1, y1, x2, y2] = bbox;
    return {
        left: x1 * scale + offsetX,
        top: y1 * scale + offsetY,
        width: (x2 - x1) * scale,
        height: (y2 - y1) * scale,
    };
}

const PAD = 6; // breathing room around the detected box
const TINT = "rgba(0,0,0,0.6)";

/**
 * Apple-card-scanner style result: the photo dims except for the plate,
 * which gets a glowing outline that animates in.
 */
export function PlateHighlight({ bbox, photo, frame }: { bbox: number[]; photo: Size; frame: Size }) {
    // Held in state, not a ref: the React Compiler forbids reading refs during render
    const [appear] = useState(() => new Animated.Value(0));

    useEffect(() => {
        appear.setValue(0);
        Animated.timing(appear, {
            toValue: 1,
            duration: 380,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
        }).start();
    }, [appear, bbox]);

    const r = coverRect(bbox, photo, frame);
    const left = Math.max(0, r.left - PAD);
    const top = Math.max(0, r.top - PAD);
    const right = Math.min(frame.width, r.left + r.width + PAD);
    const bottom = Math.min(frame.height, r.top + r.height + PAD);

    const scale = appear.interpolate({ inputRange: [0, 1], outputRange: [1.12, 1] });

    return (
        <Animated.View style={[StyleSheet.absoluteFill, { opacity: appear }]} pointerEvents="none">
            {/* Four tint panels around the plate, leaving it at full brightness */}
            <View style={[styles.tint, { left: 0, top: 0, right: 0, height: top }]} />
            <View style={[styles.tint, { left: 0, top: bottom, right: 0, bottom: 0 }]} />
            <View style={[styles.tint, { left: 0, top, width: left, height: bottom - top }]} />
            <View style={[styles.tint, { left: right, top, right: 0, height: bottom - top }]} />

            <Animated.View
                style={[
                    styles.outline,
                    { left, top, width: right - left, height: bottom - top, transform: [{ scale }] },
                ]}
            />
        </Animated.View>
    );
}

const styles = StyleSheet.create({
    tint: { position: "absolute", backgroundColor: TINT },
    outline: {
        position: "absolute",
        borderWidth: 3,
        borderColor: "#facc15",
        borderRadius: 10,
        shadowColor: "#facc15",
        shadowOpacity: 0.9,
        shadowRadius: 12,
        shadowOffset: { width: 0, height: 0 },
    },
});
