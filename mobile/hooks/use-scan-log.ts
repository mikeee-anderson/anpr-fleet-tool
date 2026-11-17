import { useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { File, Paths } from "expo-file-system";

import { BACKEND_URL, apiHeaders } from "@/constants/config";
import { fetchJsonWithRetry } from "@/lib/fetch-json";

/**
 * Today's logs from this phone, and the offline queue.
 *
 * Car park basements have no signal, so a log that can't be sent is kept on
 * the phone ("waiting") and sent automatically when signal returns. Everything
 * is saved to a file, so nothing is lost if the app closes, and a new day
 * starts with an empty list.
 */
export type LogEntry = {
    id: string;
    plate: string;
    at: string; // ISO time it was logged on the phone
    status: "sent" | "waiting" | "undone" | "rejected";
    payload: Record<string, unknown>;
    range?: string | null; // the Sheet row, for undo
};

export type LogResult =
    // no_sheet: kept on the phone until this month's sheet is started
    | { result: "sent" | "waiting" | "duplicate" | "no_sheet"; entry: LogEntry }
    | { result: "rejected"; message: string };

const logFile = new File(Paths.document, "fleetscan-log.json");
const RETRY_EVERY_MS = 20000;
const today = () => new Date().toDateString();

// One attempt to send: "network" means try again later
async function post(path: string, body: object) {
    try {
        const { ok, status, data } = await fetchJsonWithRetry(
            `${BACKEND_URL}${path}`,
            {
                method: "POST",
                headers: apiHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify(body),
            },
            { retries: 1, backoffMs: 1000, timeoutMs: 10000 }
        );
        if (status === 401) {
            return { status: "unauthorized", message: "This app's key isn't accepted by the server (EXPO_PUBLIC_API_KEY)." };
        }
        return ok ? data : { status: "network" }; // 5xx: server trouble, retry later
    } catch {
        return { status: "network" };
    }
}

// Worth sending again later rather than giving up on: no signal, or no
// sheet for this month yet
const tryLater = (status: string | undefined) => status === "network" || status === "no_sheet";

export function useScanLog() {
    const [entries, setEntries] = useState<LogEntry[]>([]);
    // latest entries for async callbacks; state drives the screen
    const latest = useRef<LogEntry[]>([]);
    const flushing = useRef(false);

    const commit = (next: LogEntry[]) => {
        latest.current = next;
        setEntries(next);
        try {
            logFile.write(JSON.stringify({ day: today(), entries: next }));
        } catch {
            // kept in memory for this session
        }
    };
    const update = (id: string, change: Partial<LogEntry>) =>
        commit(latest.current.map((e) => (e.id === id ? { ...e, ...change } : e)));

    // Send waiting logs, oldest first; stop at the first one that can't get through
    const flush = async () => {
        if (flushing.current) return;
        flushing.current = true;
        try {
            for (const entry of [...latest.current].reverse()) {
                if (entry.status !== "waiting") continue;
                const data = await post("/anpr/log", entry.payload);
                if (tryLater(data?.status)) break;
                if (data?.status === "ok" || data?.status === "duplicate_ignored") {
                    update(entry.id, { status: "sent", range: data.range ?? null });
                } else {
                    update(entry.id, { status: "rejected" });
                }
            }
        } finally {
            flushing.current = false;
        }
    };

    useEffect(() => {
        if (logFile.exists) {
            logFile
                .text()
                .then((text) => {
                    const saved = JSON.parse(text);
                    // waiting logs from an earlier day still need sending
                    const kept: LogEntry[] =
                        saved.day === today()
                            ? saved.entries ?? []
                            : (saved.entries ?? []).filter((e: LogEntry) => e.status === "waiting");
                    latest.current = kept;
                    setEntries(kept);
                    flush();
                })
                .catch(() => {});
        }
        const timer = setInterval(() => latest.current.some((e) => e.status === "waiting") && flush(), RETRY_EVERY_MS);
        const sub = AppState.addEventListener("change", (s) => s === "active" && flush());
        return () => {
            clearInterval(timer);
            sub.remove();
        };
        // flush only reads refs, so running this once is correct
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const log = async (payload: Record<string, unknown> & { plate_text: string }): Promise<LogResult> => {
        const entry: LogEntry = {
            id: `${Date.now()}-${payload.plate_text}`,
            plate: payload.plate_text,
            at: new Date().toISOString(),
            status: "waiting",
            payload,
        };
        commit([entry, ...latest.current]);

        const data = await post("/anpr/log", payload);
        if (data?.status === "network") return { result: "waiting", entry };
        if (data?.status === "no_sheet") return { result: "no_sheet", entry };
        if (data?.status === "ok" || data?.status === "duplicate_ignored") {
            const sent = { ...entry, status: "sent" as const, range: data.range ?? null };
            update(entry.id, sent);
            return { result: data.status === "ok" ? "sent" : "duplicate", entry: sent };
        }
        update(entry.id, { status: "rejected" });
        return { result: "rejected", message: data?.message ?? "The server refused this plate." };
    };

    // Undo: drop a waiting log, or clear a sent one from the Sheet
    const undo = async (id: string): Promise<"undone" | "failed" | "mismatch"> => {
        const entry = latest.current.find((e) => e.id === id);
        if (!entry) return "failed";
        if (entry.status === "waiting") {
            update(id, { status: "undone" });
            return "undone";
        }
        const data = await post("/anpr/log/undo", { plate_text: entry.plate, range: entry.range ?? null });
        if (data?.status === "ok") {
            update(id, { status: "undone" });
            return "undone";
        }
        return data?.status === "mismatch" ? "mismatch" : "failed";
    };

    // When this plate was logged today, e.g. "09:12", or null
    const loggedToday = (plate: string) => {
        const hit = latest.current.find((e) => e.plate === plate && (e.status === "sent" || e.status === "waiting"));
        return hit ? formatTime(hit.at) : null;
    };

    const active = entries.filter((e) => e.status === "sent" || e.status === "waiting");
    return {
        entries,
        loggedCount: active.length,
        waitingCount: entries.filter((e) => e.status === "waiting").length,
        log,
        flush,
        undo,
        loggedToday,
    };
}

export const formatTime = (iso: string) =>
    new Date(iso).toLocaleTimeString("en-NZ", { hour: "2-digit", minute: "2-digit" });
