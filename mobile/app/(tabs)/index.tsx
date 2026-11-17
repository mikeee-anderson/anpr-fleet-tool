import React, { useRef, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Image,
    Linking,
    StyleSheet,
    Text,
    View,
    Pressable,
} from "react-native";
import { CameraView, FocusMode, useCameraPermissions } from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import { File } from "expo-file-system";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ManualEntry } from "@/components/manual-entry";
import { PlateCard, LOOKALIKES, STANDARD_PLATE, flaggedPositions } from "@/components/plate-card";
import { PlateHighlight } from "@/components/plate-highlight";
import { IconSymbol } from "@/components/ui/icon-symbol";
import { Palette, TAP, camera, font, radius, space, useColors } from "@/constants/brand";
import { useScanFeedback } from "@/hooks/use-scan-feedback";
import { useScanLog } from "@/hooks/use-scan-log";
import { TodayLog } from "@/components/today-log";
import { SheetSettings } from "@/components/sheet-settings";
import { fetchJsonWithRetry } from "@/lib/fetch-json";
import { BACKEND_URL } from "@/constants/config";

type Photo = { uri: string; width: number; height: number };

type BestDetection = {
    bbox: number[];
    yolo_conf: number;
    // raw_text: what OCR saw, when the server corrected the plate format
    ocr: { text: string; ocr_conf: number; raw_text?: string } | null;
};

export default function HomeScreen() {
    const insets = useSafeAreaInsets();
    // Light or dark, following the phone; the styles are built from it
    const color = useColors();
    const styles = makeStyles(color);
    const cameraRef = useRef<CameraView>(null);
    const [permission, requestPermission] = useCameraPermissions();
    const [cameraReady, setCameraReady] = useState(false);
    const [torchOn, setTorchOn] = useState(false);
    // "off" is continuous autofocus; switching to "on" focuses once more
    const [focusMode, setFocusMode] = useState<FocusMode>("off");
    const [refocusing, setRefocusing] = useState(false);
    const feedback = useScanFeedback();
    // The result card appears a beat after the plate is highlighted, so the
    // highlight registers as "got it" before the next step (Scandit found
    // instant transitions felt like the app scanned "too fast")
    const [showResult, setShowResult] = useState(false);

    // Typed fallback, and a scan that failed with what to do next
    const [manualOpen, setManualOpen] = useState(false);
    // "edit" corrects the scanned read; "new" types a plate from scratch
    const [manualMode, setManualMode] = useState<"new" | "edit">("new");
    // The scanned read after staff corrected it, and the character being fixed
    const [correctedPlate, setCorrectedPlate] = useState<string | null>(null);
    const [editIndex, setEditIndex] = useState<number | null>(null);
    const [typedPlate, setTypedPlate] = useState<string | null>(null);
    const scanLog = useScanLog();
    const [todayOpen, setTodayOpen] = useState(false);
    const [settingsOpen, setSettingsOpen] = useState(false);
    // Short confirmation after logging, with an undo (undo beats a confirm step)
    const [toast, setToast] = useState<{ id: string; text: string; canUndo: boolean } | null>(null);
    const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    // fix: a one-tap remedy offered with the message (turn the torch on)
    const [issue, setIssue] = useState<{ title: string; message: string; canRetry: boolean; fix?: "torch" } | null>(
        null
    );

    // photo set = the captured frame is shown instead of the live camera.
    // Its pixel size maps the server's plate bbox onto the screen.
    const [photo, setPhoto] = useState<Photo | null>(null);
    const [frame, setFrame] = useState<{ width: number; height: number } | null>(null);
    const [bestDetection, setBestDetection] = useState<BestDetection | null>(null);

    const [loadingDetect, setLoadingDetect] = useState(false);
    const [loadingLog, setLoadingLog] = useState(false);

    // Back to the live camera for the next plate
    const resetToLive = () => {
        setPhoto(null);
        setBestDetection(null);
        setShowResult(false);
        setTypedPlate(null);
        setCorrectedPlate(null);
        setEditIndex(null);
        setIssue(null);
    };

    // A scan failed: keep the photo on screen and say what to do next
    const showIssue = (title: string, message: string, canRetry: boolean, fix?: "torch") => {
        feedback.failed();
        setIssue({ title, message, canRetry, fix });
    };

    const openManual = (mode: "new" | "edit") => {
        setManualMode(mode);
        setEditIndex(null);
        setManualOpen(true);
    };

    const useTypedPlate = (plate: string) => {
        setManualOpen(false);
        if (manualMode === "edit") {
            setCorrectedPlate(plate);
            return;
        }
        setIssue(null);
        setBestDetection(null);
        setTypedPlate(plate);
        setShowResult(true);
    };

    // Tap to refocus. expo-camera can't focus on a chosen point, so a tap
    // re-runs autofocus on the scene and the brackets flash to confirm it.
    const refocus = () => {
        setFocusMode("on");
        setRefocusing(true);
        setTimeout(() => {
            setFocusMode("off");
            setRefocusing(false);
        }, 600);
    };

    // Capture from the in-app camera (no native camera screen)
    const takePhotoAndDetect = async () => {
        if (!cameraRef.current || !cameraReady) return;
        feedback.captured();
        const shot = await cameraRef.current.takePictureAsync({ quality: 0.7, shutterSound: false });
        if (shot?.uri) await detectPhoto({ uri: shot.uri, width: shot.width, height: shot.height });
    };

    // Use a photo already on the phone (and the only way to test in the simulator, which has no camera)
    const pickPhotoAndDetect = async () => {
        const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.7 });
        if (result.canceled) return;
        const asset = result.assets[0];
        await detectPhoto({ uri: asset.uri, width: asset.width, height: asset.height });
    };

    const detectPhoto = async (shot: Photo) => {
        setPhoto(shot);
        setBestDetection(null);
        setShowResult(false);
        setIssue(null);
        setCorrectedPlate(null);
        setEditIndex(null);
        setLoadingDetect(true);

        try {
            // SDK 58's fetch only accepts Blob-like parts, not { uri, name, type };
            // expo-file-system's File reads the photo and supplies its name and type
            const formData = new FormData();
            formData.append("file", new File(shot.uri));

            const { ok, data } = await fetchJsonWithRetry(
                `${BACKEND_URL}/anpr/detect`,
                { method: "POST", body: formData },
                // CPU inference plus a possible cold start can exceed 25 s; a timeout re-uploads the photo
                { retries: 3, backoffMs: 1200, timeoutMs: 45000 }
            );

            if (!ok) {
                showIssue(
                    "The server had a problem",
                    "It couldn't read this photo. Try again, or type the plate.",
                    true
                );
                return;
            }

            if (data?.status === "needs_rescan") {
                // Show what the model did see, so a near miss is explainable
                const top: BestDetection | undefined = data?.detections?.[0];
                if (data?.hint === "too_dark") {
                    showIssue("Too dark to read", "Turn on the torch, then scan again.", false, "torch");
                } else if (data?.hint === "too_far") {
                    showIssue("Plate too small", "Move closer so the plate fills the frame, then scan again.", false);
                } else if (top?.ocr?.text) {
                    showIssue(
                        "Couldn't read the plate clearly",
                        `Closest read: ${top.ocr.text}. Move closer, hold steady or turn on the torch, then scan again.`,
                        false
                    );
                } else {
                    showIssue(
                        "No plate found",
                        "Fill the frame with the plate, hold steady, and scan again.",
                        false
                    );
                }
                return;
            }

            if (data?.status === "ok" && data?.best) {
                setBestDetection(data.best);
                feedback.locked();
                setTimeout(() => setShowResult(true), 150);
                return;
            }

            showIssue("The server had a problem", "Unexpected reply. Try again, or type the plate.", true);
        } catch {
            showIssue(
                "Can't reach the server",
                "Check you have signal or Wi-Fi, then try again. In a basement, type the plate instead.",
                true
            );
            resetToLive();
        } finally {
            setLoadingDetect(false);
        }
    };

    // Same plate already logged today from this phone: each car goes on
    // today's sheet once, so say so instead of sending it again
    const confirmAndLog = () => {
        const plate = typedPlate ?? correctedPlate ?? bestDetection?.ocr?.text;
        const earlier = plate ? scanLog.loggedToday(plate) : null;
        if (plate && earlier) {
            Alert.alert(`${plate} is already logged`, `It went on today's sheet at ${earlier}.`, [
                { text: "OK", onPress: resetToLive },
            ]);
            return;
        }
        sendLog();
    };

    const showToast = (id: string, text: string, canUndo: boolean) => {
        if (toastTimer.current) clearTimeout(toastTimer.current);
        setToast({ id, text, canUndo });
        toastTimer.current = setTimeout(() => setToast(null), 6000);
    };

    const undoFromToast = async (id: string) => {
        setToast(null);
        const outcome = await scanLog.undo(id);
        if (outcome === "undone") showToast(id, "Undone", false);
        else if (outcome === "mismatch") Alert.alert("Not undone", "That row in the Sheet has changed, so nothing was removed.");
        else Alert.alert("Couldn't undo", "No signal or the server didn't answer. Try again from Today.");
    };

    const undoFromList = async (id: string) => {
        const outcome = await scanLog.undo(id);
        if (outcome === "mismatch") Alert.alert("Not undone", "That row in the Sheet has changed, so nothing was removed.");
        else if (outcome === "failed") Alert.alert("Couldn't undo", "No signal or the server didn't answer. Try again.");
    };

    const sendLog = async () => {
        const plateText = typedPlate ?? correctedPlate ?? bestDetection?.ocr?.text;
        if (!plateText) return;

        const payload =
            typedPlate || !bestDetection?.ocr
                ? { plate_text: plateText, yolo_conf: 0, ocr_conf: 0, bbox: [], source: "mobile_manual" }
                : {
                      plate_text: plateText,
                      yolo_conf: bestDetection.yolo_conf,
                      ocr_conf: bestDetection.ocr.ocr_conf,
                      bbox: bestDetection.bbox,
                      // corrected reads are kept apart so OCR accuracy can be measured
                      source: correctedPlate ? "mobile_corrected" : "mobile_confirm",
                      ...(correctedPlate ? { ocr_text: bestDetection.ocr.text } : {}),
                  };

        setLoadingLog(true);
        const outcome = await scanLog.log(payload);
        setLoadingLog(false);

        if (outcome.result === "rejected") {
            Alert.alert("Not logged", outcome.message);
            return;
        }
        const text = {
            sent: `${plateText} logged`,
            waiting: `${plateText} saved on this phone. It will send when you have signal.`,
            duplicate: `${plateText} is already on today's sheet`,
            no_sheet: `${plateText} saved on this phone. Tap Sheet to start this month's, then it sends.`,
        }[outcome.result];
        showToast(outcome.entry.id, text, outcome.result !== "duplicate");
        resetToLive(); // straight back to scanning the next car
    };

    const ocr = bestDetection?.ocr;
    const plateText = typedPlate ?? correctedPlate ?? ocr?.text;
    // Typed or already-corrected plates are what staff meant: nothing to flag
    const flagged =
        plateText && !typedPlate && !correctedPlate ? flaggedPositions(plateText, ocr?.raw_text) : new Set<number>();
    // The aiming cutout: plate-shaped (NZ plates are about 3:1), centred in the frame
    const guide = frame
        ? (() => {
              const width = frame.width * 0.82;
              const height = width / 3;
              return {
                  left: (frame.width - width) / 2,
                  top: (frame.height - height) / 2,
                  width,
                  height,
                  band: Math.max(frame.width, frame.height),
              };
          })()
        : null;
    const editChar = editIndex !== null && plateText ? plateText[editIndex] : null;

    const replaceChar = (c: string) => {
        if (editIndex === null || !plateText) return;
        const fixed = plateText.slice(0, editIndex) + c + plateText.slice(editIndex + 1);
        setCorrectedPlate(fixed);
        setEditIndex(null);
    };
    const status = plateText
        ? statusLines({
              color,
              standard: STANDARD_PLATE.test(plateText),
              lowConfidence: !typedPlate && !correctedPlate && !!ocr && ocr.ocr_conf < LOW_OCR_CONF,
              origin: typedPlate ? "Typed in" : correctedPlate ? "Corrected by you" : null,
          })
        : [];

    return (
        <View style={styles.screen}>
            {/* Live camera runs edge to edge under the controls; after a scan it shows the captured frame */}
            <View
                style={styles.viewfinder}
                onLayout={(e) => setFrame({ width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height })}
            >
                {photo ? (
                    <Image source={{ uri: photo.uri }} style={StyleSheet.absoluteFill} resizeMode="cover" />
                ) : permission?.granted ? (
                    <CameraView
                        ref={cameraRef}
                        style={StyleSheet.absoluteFill}
                        facing="back"
                        enableTorch={torchOn}
                        autofocus={focusMode}
                        onCameraReady={() => setCameraReady(true)}
                    />
                ) : permission ? (
                    <View style={styles.permissionBox}>
                        <IconSymbol name="camera.fill" size={40} color={color.text} />
                        <Text style={styles.permissionTitle} accessibilityRole="header">
                            FleetScan needs the camera
                        </Text>
                        <Text style={styles.permissionText}>
                            {permission.canAskAgain
                                ? "It reads plates from the camera. Photos are only sent to read the plate."
                                : "Camera access is off for FleetScan. Turn it on in Settings, or type plates instead."}
                        </Text>
                        <Pressable
                            style={[styles.primaryBtn, styles.permissionBtn]}
                            onPress={permission.canAskAgain ? requestPermission : () => Linking.openSettings()}
                            accessibilityRole="button"
                        >
                            <Text style={styles.fillText} maxFontSizeMultiplier={1.4}>
                                {permission.canAskAgain ? "Allow camera" : "Open Settings"}
                            </Text>
                        </Pressable>
                        <Pressable
                            style={[styles.secondaryBtn, styles.permissionBtn]}
                            onPress={() => openManual("new")}
                            accessibilityRole="button"
                        >
                            <Text style={styles.secondaryText} maxFontSizeMultiplier={1.4}>
                                Type a plate instead
                            </Text>
                        </Pressable>
                    </View>
                ) : null}

                {/* Plate-shaped guide while aiming */}
                {/* Result: dim everything except the plate the server found */}
                {photo && bestDetection && frame && (
                    <PlateHighlight bbox={bestDetection.bbox} photo={photo} frame={frame} />
                )}

                {/* Aiming guide only while aiming: not over a typed plate or an error */}
                {!photo && !plateText && !issue && permission?.granted && (
                    <Pressable style={styles.guideLayer} onPress={refocus} accessibilityLabel="Tap to refocus">
                        {guide && (
                            <>
                                {/* Dim the feed except a clear, plate-shaped cutout to aim with.
                                    One view with a screen-sized border: its inner edge is the
                                    cutout, with the brackets' rounded corners (see PlateHighlight) */}
                                <View
                                    style={[
                                        styles.dimRing,
                                        {
                                            left: guide.left - guide.band,
                                            top: guide.top - guide.band,
                                            width: guide.width + 2 * guide.band,
                                            height: guide.height + 2 * guide.band,
                                            borderWidth: guide.band,
                                            borderRadius: radius.md + guide.band,
                                        },
                                    ]}
                                />
                                {/* Scan brackets, as in the FleetScan logo */}
                                <View
                                    style={[
                                        styles.guideFrame,
                                        { left: guide.left, top: guide.top, width: guide.width, height: guide.height },
                                    ]}
                                >
                                    <View style={[styles.corner, styles.cornerTL, refocusing && styles.cornerFocus]} />
                                    <View style={[styles.corner, styles.cornerTR, refocusing && styles.cornerFocus]} />
                                    <View style={[styles.corner, styles.cornerBL, refocusing && styles.cornerFocus]} />
                                    <View style={[styles.corner, styles.cornerBR, refocusing && styles.cornerFocus]} />
                                </View>
                            </>
                        )}
                        <View style={[styles.hintArea, guide && { top: guide.top + guide.height }]}>
                            <Text style={styles.hintText}>Line up the number plate in the frame</Text>
                            <Pressable
                                style={styles.manualLink}
                                onPress={() => openManual("new")}
                                accessibilityRole="button"
                                accessibilityLabel="Type the plate instead"
                            >
                                <IconSymbol name="keyboard" size={18} color={camera.accent} />
                                <Text style={styles.manualLinkText}>{"Can't scan it? Type the plate"}</Text>
                            </Pressable>
                        </View>
                    </Pressable>
                )}

                {loadingDetect && (
                    <View style={styles.readingOverlay} accessibilityLiveRegion="polite">
                        <ActivityIndicator size="large" color={camera.text} />
                        <Text style={styles.readingText}>Photo captured · reading plate…</Text>
                    </View>
                )}
            </View>

            <View style={[styles.header, { paddingTop: insets.top + 6 }]} pointerEvents="box-none">
                <Image
                    source={require("@/assets/images/fleetscan-logo.png")}
                    style={styles.logo}
                    resizeMode="contain"
                    accessibilityLabel="FleetScan"
                />
                <Pressable
                    style={styles.soundBtn}
                    onPress={feedback.toggleSound}
                    accessibilityRole="switch"
                    accessibilityState={{ checked: feedback.soundOn }}
                    accessibilityLabel="Scan sound"
                >
                    <IconSymbol
                        name={feedback.soundOn ? "speaker.wave.2.fill" : "speaker.slash.fill"}
                        size={20}
                        color={feedback.soundOn ? camera.text : camera.textMuted}
                    />
                </Pressable>
            </View>

            <View style={[styles.bottomOverlay, { paddingBottom: Math.max(insets.bottom, 16) }]}>
                {/* Result: the plate styled like a NZ plate, with a plain-English confidence */}
                {/* A failed scan, with what to do next */}
                {issue && (
                    <View style={styles.issueCard} accessibilityLiveRegion="polite">
                        <Text style={styles.issueTitle}>{issue.title}</Text>
                        <Text style={styles.issueText}>{issue.message}</Text>
                    </View>
                )}

                {plateText && showResult && (
                    <View style={styles.resultCard}>
                        <PlateCard
                            text={plateText}
                            flagged={flagged}
                            selected={editIndex}
                            onPressChar={(i) => setEditIndex(editIndex === i ? null : i)}
                        />

                        {/* Fixing one character: offer its look-alikes, or retype the plate */}
                        {editChar ? (
                            <View style={styles.fixRow}>
                                <Text style={styles.fixLabel}>{`Change ${editChar} to`}</Text>
                                {(LOOKALIKES[editChar] ?? []).map((alt) => (
                                    <Pressable
                                        key={alt}
                                        style={styles.fixChoice}
                                        onPress={() => replaceChar(alt)}
                                        accessibilityLabel={`Change to ${alt}`}
                                    >
                                        <Text style={styles.fixChoiceText}>{alt}</Text>
                                    </Pressable>
                                ))}
                                <Pressable style={styles.fixEdit} onPress={() => openManual("edit")}>
                                    <IconSymbol name="keyboard" size={18} color={color.accent} />
                                    <Text style={styles.fixEditText}>Retype</Text>
                                </Pressable>
                            </View>
                        ) : flagged.size > 0 ? (
                            <Text style={styles.fixHint}>Check the underlined characters. Tap one to fix it.</Text>
                        ) : (
                            <Text style={styles.fixHint}>Tap a character to fix it</Text>
                        )}

                        {/* Defined states only: format, an unclear read and how the plate was entered */}
                        {status.map((line) => (
                            <View key={line.text} style={styles.statusRow}>
                                <IconSymbol name={line.icon} size={18} color={line.color} />
                                <Text style={[styles.statusText, { color: line.color }]}>{line.text}</Text>
                            </View>
                        ))}
                    </View>
                )}

                {/* Just logged: confirmation with undo */}
                {toast && !plateText && (
                    <View style={styles.toast} accessibilityLiveRegion="polite">
                        <Text style={styles.toastText}>{toast.text}</Text>
                        {toast.canUndo && (
                            <Pressable onPress={() => undoFromToast(toast.id)} style={styles.toastUndo} accessibilityRole="button">
                                <Text style={styles.toastUndoText}>Undo</Text>
                            </Pressable>
                        )}
                    </View>
                )}

                {/* Today's count, anything still waiting, and where scans go */}
                {!plateText && !issue && !toast && (
                    <View style={styles.todayStrip}>
                        {scanLog.entries.length > 0 ? (
                            <Pressable
                                style={styles.todayLink}
                                onPress={() => setTodayOpen(true)}
                                accessibilityRole="button"
                                accessibilityHint="Shows today's logs and lets you undo one"
                            >
                                <Text style={styles.todayText}>
                                    {`Today: ${scanLog.loggedCount} logged`}
                                    {scanLog.waitingCount > 0 && (
                                        <Text style={styles.todayWaiting}>{` · ${scanLog.waitingCount} waiting`}</Text>
                                    )}
                                </Text>
                                <IconSymbol name="chevron.right" size={16} color={color.textMuted} />
                            </Pressable>
                        ) : (
                            <View style={styles.flex1} />
                        )}
                        <Pressable
                            style={styles.sheetLink}
                            onPress={() => setSettingsOpen(true)}
                            accessibilityRole="button"
                            accessibilityLabel="Audit sheet"
                            accessibilityHint="Shows which spreadsheet scans go to, and starts a new month"
                        >
                            <IconSymbol name="tablecells" size={18} color={color.accent} />
                            <Text style={styles.sheetLinkText}>Sheet</Text>
                        </Pressable>
                    </View>
                )}

                {/* Actions pinned to the bottom, in thumb reach, above the home indicator */}
                <View style={styles.bottomBar}>
                    {issue ? (
                        <View style={styles.actionRow}>
                            <Pressable
                                style={[styles.secondaryBtn, styles.flex1]}
                                onPress={() => openManual("new")}
                                accessibilityRole="button"
                            >
                                <Text style={styles.secondaryText} maxFontSizeMultiplier={1.4}>
                                    Type plate
                                </Text>
                            </Pressable>
                            <Pressable
                                style={[styles.primaryBtn, styles.flex1]}
                                accessibilityRole="button"
                                onPress={() => {
                                    if (issue.fix === "torch") setTorchOn(true);
                                    if (issue.canRetry && photo) detectPhoto(photo);
                                    else resetToLive();
                                }}
                            >
                                <Text style={styles.fillText} maxFontSizeMultiplier={1.4}>
                                    {issue.fix === "torch" && !torchOn ? "Turn on torch" : issue.canRetry ? "Try again" : "Scan again"}
                                </Text>
                            </Pressable>
                        </View>
                    ) : plateText ? (
                        <View style={styles.actionRow}>
                            <Pressable
                                style={[styles.secondaryBtn, styles.flex1]}
                                onPress={resetToLive}
                                disabled={loadingLog}
                                accessibilityRole="button"
                                accessibilityHint="Discards this reading and goes back to the camera"
                            >
                                <Text style={styles.secondaryText} maxFontSizeMultiplier={1.4}>
                                    Rescan
                                </Text>
                            </Pressable>
                            <Pressable
                                style={({ pressed }) => [styles.successBtn, styles.flex2, (pressed || loadingLog) && { opacity: 0.8 }]}
                                onPress={confirmAndLog}
                                disabled={loadingLog}
                                accessibilityRole="button"
                                accessibilityLabel={`Log ${plateText ?? ""} to yard audit`}
                            >
                                {loadingLog ? <ActivityIndicator color={color.onFill} /> : <Text style={styles.fillText} maxFontSizeMultiplier={1.4}>Log to yard audit</Text>}
                            </Pressable>
                        </View>
                    ) : !permission?.granted ? null /* the permission screen has its own buttons */ : (
                        <View style={styles.actionRow}>
                            <Pressable
                                style={({ pressed }) => [styles.iconBtn, (pressed || loadingDetect) && { opacity: 0.6 }]}
                                onPress={pickPhotoAndDetect}
                                disabled={loadingDetect}
                                accessibilityRole="button"
                                accessibilityLabel="Choose a photo from the library"
                            >
                                <IconSymbol name="photo.on.rectangle" size={26} color={color.text} />
                            </Pressable>
                            <Pressable
                                style={({ pressed }) => [
                                    styles.primaryBtn,
                                    styles.flex1,
                                    (pressed || loadingDetect || !cameraReady) && { opacity: 0.6 },
                                ]}
                                onPress={takePhotoAndDetect}
                                disabled={loadingDetect || !cameraReady || !!photo}
                                accessibilityRole="button"
                                accessibilityLabel="Scan plate"
                                accessibilityState={{ busy: loadingDetect }}
                            >
                                <View style={styles.btnRow}>
                                    <IconSymbol name="camera.fill" size={22} color={color.onFill} />
                                    <Text style={styles.fillText} maxFontSizeMultiplier={1.4}>
                                        {loadingDetect ? "Reading…" : "Scan Plate"}
                                    </Text>
                                </View>
                            </Pressable>
                            {/* Torch for night returns and dark car parks */}
                            <Pressable
                                style={styles.iconBtn}
                                onPress={() => setTorchOn((on) => !on)}
                                accessibilityRole="switch"
                                accessibilityState={{ checked: torchOn }}
                                accessibilityLabel="Torch"
                            >
                                <IconSymbol
                                    name={torchOn ? "flashlight.on.fill" : "flashlight.off.fill"}
                                    size={26}
                                    color={torchOn ? color.accent : color.text}
                                />
                            </Pressable>
                        </View>
                    )}
                </View>
            </View>

            <TodayLog
                visible={todayOpen}
                entries={scanLog.entries}
                onUndo={undoFromList}
                onClose={() => setTodayOpen(false)}
            />
            <SheetSettings
                visible={settingsOpen}
                onClose={() => setSettingsOpen(false)}
                onMonthStarted={scanLog.flush}
            />
            <ManualEntry
                key={manualOpen ? "open" : "closed"} // fresh text each time it opens
                visible={manualOpen}
                initial={manualMode === "edit" ? (plateText ?? "") : ""}
                onCancel={() => setManualOpen(false)}
                onSubmit={useTypedPlate}
            />
        </View>
    );
}

const LOW_OCR_CONF = 0.6; // below this, the server's read is worth a second look

type StatusLine = { icon: "exclamationmark.triangle.fill" | "keyboard"; text: string; color: string };

// The lines under the plate. Each one is a defined state that tells staff
// something to act on; with nothing to say, there are no lines.
// (A vehicle lookup, e.g. make/model/colour, can slot in here later.)
function statusLines({
    color,
    standard,
    lowConfidence,
    origin,
}: {
    color: Palette;
    standard: boolean;
    lowConfidence: boolean;
    origin: string | null;
}): StatusLine[] {
    const warn = color.warning;
    const muted = color.textMuted;
    const lines: StatusLine[] = [];

    // Personalised plates exist, so this warns rather than blocks
    if (!standard) {
        lines.push({
            icon: "exclamationmark.triangle.fill",
            text: "Not a standard plate format. Fine if personalised.",
            color: warn,
        });
    }
    if (lowConfidence) {
        lines.push({ icon: "exclamationmark.triangle.fill", text: "Unclear read. Check it against the car.", color: warn });
    }
    if (origin) lines.push({ icon: "keyboard", text: origin, color: muted });
    return lines;
}

// Fills the parent (React Native 0.88 dropped StyleSheet.absoluteFillObject)
const fill = { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 } as const;

const makeStyles = (color: Palette) =>
    StyleSheet.create({
        screen: { flex: 1, backgroundColor: color.bg },
        // The logo and sound toggle float over the camera: no bar, so the camera reaches the top
        header: { position: "absolute", top: 0, left: 0, right: 0, alignItems: "center", paddingBottom: space.sm },
        logo: { height: 22, aspectRatio: 751 / 144, opacity: 0.9 },
        soundBtn: {
            position: "absolute",
            left: space.sm,
            bottom: 0,
            width: TAP,
            height: TAP,
            alignItems: "center",
            justifyContent: "center",
        },
        // Text and the plate card sit on this, so it's nearly solid
        bottomOverlay: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: color.panel },

        viewfinder: { ...fill, backgroundColor: camera.empty },
        guideLayer: { ...fill, overflow: "hidden" },
        dimRing: { position: "absolute", borderColor: camera.dim },
        guideFrame: { position: "absolute" },
        hintArea: { position: "absolute", left: 0, right: 0, alignItems: "center", paddingTop: space.lg },
        corner: { position: "absolute", width: 32, height: 24, borderColor: camera.accent },
        cornerTL: { top: 0, left: 0, borderTopWidth: 4, borderLeftWidth: 4, borderTopLeftRadius: radius.md },
        cornerTR: { top: 0, right: 0, borderTopWidth: 4, borderRightWidth: 4, borderTopRightRadius: radius.md },
        cornerBL: { bottom: 0, left: 0, borderBottomWidth: 4, borderLeftWidth: 4, borderBottomLeftRadius: radius.md },
        cornerBR: { bottom: 0, right: 0, borderBottomWidth: 4, borderRightWidth: 4, borderBottomRightRadius: radius.md },
        cornerFocus: { borderColor: camera.text },
        hintText: { ...font.label, color: camera.text },
        manualLink: {
            flexDirection: "row",
            alignItems: "center",
            gap: space.xs,
            marginTop: space.md,
            minHeight: TAP,
            paddingHorizontal: space.md,
        },
        manualLinkText: { ...font.body, fontWeight: "600", color: camera.accent },

        readingOverlay: { ...fill, backgroundColor: camera.dim, alignItems: "center", justifyContent: "center", gap: space.md },
        readingText: { ...font.label, color: camera.text },
        permissionBox: {
            flex: 1,
            alignItems: "center",
            justifyContent: "center",
            padding: space.lg,
            gap: space.md,
            backgroundColor: color.bg,
        },
        permissionText: { ...font.body, color: color.textMuted, textAlign: "center", lineHeight: 21 },
        permissionTitle: { ...font.title, color: color.text, textAlign: "center" },
        permissionBtn: { alignSelf: "stretch" },

        issueCard: { marginHorizontal: space.md, marginTop: space.md, gap: space.xs },
        issueTitle: { ...font.title, color: color.text },
        issueText: { ...font.body, color: color.textMuted, lineHeight: 21 },

        resultCard: { marginHorizontal: space.md, marginTop: space.md, alignItems: "center", gap: space.sm },
        fixRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
        fixLabel: { ...font.body, color: color.textMuted },
        fixChoice: {
            minWidth: TAP,
            minHeight: TAP,
            borderRadius: radius.sm,
            backgroundColor: color.plate,
            alignItems: "center",
            justifyContent: "center",
        },
        fixChoiceText: { fontSize: 24, fontWeight: "900", color: color.plateInk },
        fixEdit: { flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: TAP, paddingHorizontal: space.sm },
        fixEditText: { ...font.body, fontWeight: "600", color: color.accent },
        fixHint: { ...font.caption, color: color.textMuted },
        statusRow: { flexDirection: "row", alignItems: "center", gap: space.xs },
        statusText: { ...font.body, fontWeight: "600" },

        toast: { flexDirection: "row", alignItems: "center", marginHorizontal: space.md, marginTop: space.md, gap: space.md },
        toastText: { ...font.body, fontWeight: "600", flex: 1, color: color.text },
        toastUndo: { minHeight: TAP, minWidth: 64, alignItems: "center", justifyContent: "center" },
        toastUndoText: { ...font.label, fontSize: 16, color: color.accent },
        todayStrip: {
            flexDirection: "row",
            alignItems: "center",
            gap: space.md,
            marginHorizontal: space.md,
            marginTop: space.sm,
            minHeight: TAP,
        },
        todayLink: { flex: 1, flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: TAP },
        sheetLink: { flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: TAP, paddingLeft: space.sm },
        sheetLinkText: { ...font.label, fontSize: 16, color: color.accent },
        todayText: { ...font.body, fontWeight: "600", color: color.textMuted, flexShrink: 1 },
        todayWaiting: { color: color.warning },

        bottomBar: { paddingHorizontal: space.md, paddingTop: space.md },
        actionRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
        flex1: { flex: 1 },
        flex2: { flex: 2 },
        btnRow: { flexDirection: "row", alignItems: "center", gap: space.sm },

        // Buttons by role: filled accent (primary), filled success (log), outlined (secondary), plain icon
        primaryBtn: { backgroundColor: color.accent, minHeight: 56, borderRadius: radius.lg, alignItems: "center", justifyContent: "center" },
        successBtn: { backgroundColor: color.success, minHeight: 56, borderRadius: radius.lg, alignItems: "center", justifyContent: "center" },
        secondaryBtn: {
            borderWidth: 1.5,
            borderColor: color.border,
            minHeight: 56,
            borderRadius: radius.lg,
            alignItems: "center",
            justifyContent: "center",
        },
        iconBtn: { width: 56, height: 56, alignItems: "center", justifyContent: "center" },
        fillText: { ...font.label, fontWeight: "800", color: color.onFill }, // dark text on accent/success fills
        secondaryText: { ...font.label, color: color.text },
    });
