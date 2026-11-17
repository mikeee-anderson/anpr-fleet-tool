import React, { useEffect, useState } from "react";
import { Animated, Easing, StyleSheet, View } from "react-native";
import { camera } from "@/constants/brand";

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
const RADIUS = 10; // corner radius of the outline, and of the clear cutout in the tint

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

    // The tint is one view with a border thick enough to cover the screen. Its
    // inner edge is the cutout, and a border's inner corner radius is its outer
    // radius minus its width, so the hole has exactly the outline's rounded
    // corners (four rectangular panels left the corners untinted).
    const band = Math.max(frame.width, frame.height);

    return (
        <Animated.View style={[StyleSheet.absoluteFill, styles.clip, { opacity: appear }]} pointerEvents="none">
            <View
                style={[
                    styles.tint,
                    {
                        left: left - band,
                        top: top - band,
                        width: right - left + 2 * band,
                        height: bottom - top + 2 * band,
                        borderWidth: band,
                        borderRadius: RADIUS + band,
                    },
                ]}
            />

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
    clip: { overflow: "hidden" },
    tint: { position: "absolute", borderColor: TINT },
    outline: {
        position: "absolute",
        borderWidth: 3,
        borderColor: camera.success,
        borderRadius: RADIUS,
        shadowColor: camera.success,
        shadowOpacity: 0.9,
        shadowRadius: 12,
        shadowOffset: { width: 0, height: 0 },
    },
});
