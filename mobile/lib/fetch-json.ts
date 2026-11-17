// fetch with a timeout and retries for flaky car-park signal and Cloud Run cold starts
function sleep(ms: number) {
    return new Promise((r) => setTimeout(r, ms));
}

export async function fetchJsonWithRetry(
    url: string,
    options: RequestInit,
    {
        retries = 3,
        backoffMs = 1200,
        timeoutMs = 20000,
    }: { retries?: number; backoffMs?: number; timeoutMs?: number } = {}
) {
    let lastErr: any = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const res = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(id);

            // Try parse JSON; if not JSON, treat as text
            const text = await res.text();
            let data: any = null;
            try {
                data = text ? JSON.parse(text) : null;
            } catch {
                data = { raw: text };
            }

            // Handle warming_up from backend
            if (data?.status === "warming_up") {
                if (attempt < retries) {
                    await sleep(backoffMs * (attempt + 1));
                    continue;
                }
                return { ok: false, status: res.status, data };
            }

            // Retry on common transient statuses
            if (!res.ok && [502, 503, 504].includes(res.status) && attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }

            return { ok: res.ok, status: res.status, data };
        } catch (e: any) {
            clearTimeout(id);
            lastErr = e;

            // Retry on network/timeout
            if (attempt < retries) {
                await sleep(backoffMs * (attempt + 1));
                continue;
            }
            throw lastErr;
        }
    }

    throw lastErr;
}
