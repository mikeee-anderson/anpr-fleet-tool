# FleetScan design

The rules every screen follows. Values live in [`constants/brand.ts`](constants/brand.ts). Use the tokens, not raw numbers or hex codes.

## Principles

- **The camera is the screen.** Controls float over it; nothing frames it.
- **One thing to do at a time.** Aiming shows the guide, a result shows the plate, an error shows what to do next. They never stack.
- **Every status means something.** A line under the plate is a defined state that staff can act on. If there is nothing to say, show nothing.
- **Strip before polishing.** No decoration that doesn't help someone scan a car one-handed in a car park.

## Colour

One neutral, one accent and one success colour. Amber is for "check this" only.

| Token | Value | Use |
|---|---|---|
| `bg` | `#010F2C` | Screen background |
| `surface` | `#0C1B48` | Sheets, empty camera area |
| `accent` | `#62A7EA` | Primary action, links, scan brackets. The only accent. |
| `success` | `#22C55E` | Plate locked (outline) and the log button. The only success colour. |
| `warning` | `#F59E0B` | Something to check (format, unclear read, flagged character, waiting to send). Never blocks. |
| `text` / `textMuted` | white / 70% white | Body text / secondary text |
| `onFill` | `#010F2C` | Text and icons on accent or success fills (white on these fails contrast) |
| `border` / `divider` | 35% / 15% white | Outlined buttons / list rows |
| `panel` | navy at 90% (light: 95% off-white) | The bottom panel over the camera |
| `plate`, `plateInk`, `plateSurround` | white, `#111`, `#0B0B0B` | Only for the NZ plate card |

The lemon in the logo stays in the logo; the UI doesn't use it.

**Light theme.** The app follows the phone's light or dark setting (`useColors()`), because dark screens wash out in direct sun. The roles stay the same. In light mode the accent (`#1F6FC5`) and warning (`#B45309`) are darker, and fills use white text, so everything still passes contrast on white.

**On the camera.** Anything drawn over the camera feed (brackets, the aiming hint, the logo, the sound toggle, the "reading" overlay and the lock outline) uses the `camera` tokens. It sits on a dark, dimmed image, so it keeps the dark look in both themes.

## Spacing and shape

- **8-point grid:** `xs 4 · sm 8 · md 16 · lg 24 · xl 32`. Use 4 only for tight gaps, such as an icon next to its label.
- **Radii:** `sm 8` (small keys) · `md 12` (scan brackets) · `lg 16` (buttons, sheets).
- **Tap targets:** at least 44 pt (`TAP`). Main buttons are 56 pt tall.

## Type

The iOS system font (SF) throughout. Only size and weight change.

| Token | Size / weight | Use |
|---|---|---|
| `caption` | 13 / 500 | Times, small hints |
| `body` | 15 / 500 | Messages, status lines |
| `label` | 17 / 700 | Buttons, the aiming instruction |
| `title` | 20 / 700 | Error titles, sheet titles |
| `plate` | 40 / 900 | The plate characters |

## Buttons

| Role | Look | Example |
|---|---|---|
| Primary | `accent` fill, `onFill` text, 56 pt, radius `lg` | Scan Plate, Try again, Use plate |
| Success | `success` fill, `onFill` text | Log to yard audit |
| Secondary | 1.5 pt `border` outline, white text, no fill | Rescan, Type plate, Cancel |
| Icon | Plain icon, no background, 56 pt square | Photo library, torch (accent when on), sound |
| Text link | `accent` text, optional icon | "Can't scan it? Type the plate", Undo |

There is at most one filled button per row. Cards are kept for things you act on directly, such as the plate card. Everything else is grouped with spacing.

## Accessibility

- Every control has a VoiceOver role and label, and a hint where the result isn't obvious (Rescan discards the reading). Messages that appear by themselves, such as errors, the reading overlay and the toast, are live regions, so VoiceOver reads them out.
- Text follows Dynamic Type. Plate characters are capped at 1.2× and button labels at 1.4×, so a plate always fits and buttons don't overflow.
- Tap targets are at least 44 pt.
- The app is locked to portrait, so it's held one-handed and the plate guide never rotates.
