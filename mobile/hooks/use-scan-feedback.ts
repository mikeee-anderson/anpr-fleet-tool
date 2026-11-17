import { useEffect, useState } from "react";
import { useAudioPlayer } from "expo-audio";
import * as Haptics from "expo-haptics";
import { File, Paths } from "expo-file-system";

// Car parks are loud and staff look away from the screen: confirm a scan by
// touch and sound as well as on screen. The sound can be switched off; the
// choice is kept on the phone.
const settingsFile = new File(Paths.document, "fleetscan-settings.json");

export function useScanFeedback() {
    const player = useAudioPlayer(require("@/assets/sounds/lock.wav"));
    const [soundOn, setSoundOn] = useState(true);

    useEffect(() => {
        if (!settingsFile.exists) return;
        settingsFile
            .text()
            .then((text) => setSoundOn(JSON.parse(text).soundOn !== false))
            .catch(() => {}); // unreadable settings: keep the default
    }, []);

    const toggleSound = () => {
        const next = !soundOn;
        setSoundOn(next);
        try {
            settingsFile.write(JSON.stringify({ soundOn: next }));
        } catch {
            // not saved; it still applies until the app restarts
        }
    };

    return {
        soundOn,
        toggleSound,
        // Shutter pressed
        captured: () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light),
        // Plate read: the main confirmation
        locked: () => {
            Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
            if (soundOn) {
                player.seekTo(0);
                player.play();
            }
        },
        // Nothing usable read
        failed: () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning),
    };
}
