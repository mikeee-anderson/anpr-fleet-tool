# ANPR Mobile

React Native (Expo) mobile app for the ANPR Fleet Tool. Fleet staff use the app to photograph a vehicle's number plate — the image is sent to the backend for detection and OCR, the result is shown on screen, and staff confirm to log it to Google Sheets.

---

## Structure

```
mobile/
├── app/
│   ├── (tabs)/
│   │   ├── index.tsx       # Main screen — camera capture, detection, confirm & log
│   │   ├── explore.tsx     # Explore / info screen
│   │   └── _layout.tsx     # Tab navigator layout
│   ├── _layout.tsx         # Root layout
│   └── modal.tsx           # Modal screen
├── assets/                 # Images, icons, fonts
├── components/             # Reusable UI components
│   └── ui/                 # Collapsible, IconSymbol, etc.
├── constants/              # Theme, colours, config
├── hooks/                  # Custom React hooks
├── scripts/                # Dev utility scripts
├── app.json                # Expo config
├── tsconfig.json
└── package.json
```

---

## Setup

```bash
# Install dependencies
npm install

# Start Expo dev server
npx expo start
```

Scan the QR code with **Expo Go** (iOS or Android), or press:
- `i` — open in iOS simulator
- `a` — open in Android emulator
- `w` — open in browser

---

## Configuration

The backend URL is set directly in `app/(tabs)/index.tsx`:

```ts
const BACKEND_URL = "https://your-backend-url";
```

If running the backend locally and testing on a physical device, use your machine's local network IP instead of `localhost`:

```ts
const BACKEND_URL = "http://192.168.x.x:8000";
```

---

## How It Works (`app/(tabs)/index.tsx`)

### Scan flow
1. User taps **Scan Plate** — requests camera permission, opens camera via `expo-image-picker`
2. Photo is uploaded to `POST /anpr/detect` as `multipart/form-data`
3. Backend returns the best detected plate with YOLO and OCR confidence scores
4. Plate text and confidence scores are displayed on screen

### Confirm & Log flow
5. User reviews the detected plate and taps **Confirm & Log**
6. App sends plate text, confidence scores, bbox, and `source: "mobile_confirm"` to `POST /anpr/log`
7. Backend logs to Google Sheets and returns confirmation
8. Success/duplicate/error is shown via an Alert

### Retry logic
All API calls use `fetchJsonWithRetry` which:
- Retries up to 3 times on network errors, timeouts, and 502/503/504 responses
- Handles `warming_up` responses from the backend (retries with backoff while model loads)
- Has a 20–25 second timeout per attempt

---

## Key Screens

| Screen | File | Description |
|--------|------|-------------|
| Scanner | `app/(tabs)/index.tsx` | Camera capture, plate detection, confirm & log |
| Explore | `app/(tabs)/explore.tsx` | Info and feature overview |

---

## Tech

- [Expo](https://expo.dev/) with [Expo Router](https://expo.github.io/router/) (file-based navigation)
- [expo-image-picker](https://docs.expo.dev/versions/latest/sdk/imagepicker/) — camera access
- TypeScript
- React Native new architecture enabled (`newArchEnabled: true`)
- React Compiler enabled (`reactCompiler: true`)
