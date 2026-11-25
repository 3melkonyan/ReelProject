# Design system

This document describes the Reel dashboard, email and lesson-thumbnail design. Reel uses its own logo and product identity. Colors and sizes are defined as CSS custom properties in `reel/web/static/app.css`.

## Colors

| Token / role | Light | Dark | Notes |
| --- | --- | --- | --- |
| Brand (`--brand`) | #D500C8 | #D500C8 | Primary brand color |
| Primary action (`--primary`) | #D500C8 | #D500C8 | White text on top has a 4.5:1 contrast ratio |
| Accent (`--accent`) | #B000A5 | #FF83F8 | Links and emphasis |
| Accent hover | #93048A | #FFA6FA | |
| Mint | #71FFEA | #71FFEA | Badges and decorative accents, always with dark text |
| Pink | #FF3DF3 | #FF3DF3 | Limited decoration |
| Background | #FCFCFC | #171717 | |
| Surface | #FFFFFF | #202020 | |
| Secondary surface | #F5F4F7 | #2A2A2A | |
| Main text | #171717 | #F5F5F5 | |
| Secondary text | #4D4D4D | #C4C4C4 | Supporting copy |
| Tertiary text | #68616F | #B3B3B3 | Avoid for important small text |
| Border | #E5E5E5 | #525252 | Inputs use a stronger border |
| Success | #16733C | #81E5A5 | #34C759 is reserved for decorative dots |
| Warning | #805400 | #FCD34D | |
| Error | #B52335 | #FF9CA9 | |
| Focus | #93048A | #71FFEA | Visible 3px outline |

Gradients are used only for decoration on the login panel and progress indicators. Large content surfaces are flat.

## Type and spacing

Headings use Mardoto Bold 700 and body text uses Noto Sans Armenian (variable, 400–800); both are bundled with `font-display: swap` and support Latin and Armenian. Their licenses are listed in [third-party notices](../THIRD_PARTY_NOTICES.md).

- Font sizes: 12/14/16/20/24/32/40px; body line-height 1.6, headings 1.25–1.35.
- Spacing scale: 4/8/12/16/24/32/48/64px.
- Radii: cards 24px, inputs 12px, large panels 32px; buttons and chips are pill-shaped.
- Borders divide content; shadows indicate elevation and hover.

## Components

- One clear primary action per view, outlined secondary actions, and visible hover, active, focus and disabled states.
- Touch targets are at least 44px; form fields are 48px high.
- Compact sidebar on desktop; header and bottom navigation on mobile. Text must fit a 320px-wide viewport.
- Lesson thumbnails are prominent; group and status chips are compact; progress is shown consistently. Empty states explain the next available action.
- Illustrations use CSS geometry and line SVG icons.

## Motion and themes

- Transitions use 160/240ms with `cubic-bezier(.2,.7,.2,1)` and move at most 2px. `prefers-reduced-motion` disables animation.
- Armenian and English share the same components; labels must fit both languages.
- Dark theme is the default. A manual light/dark choice is remembered and applied without reloading an active player, upload or form.

## Lesson thumbnails

Each group gets one of eighteen persistent styles (palette and shape motif). A thumbnail shows a sharp, already-blurred frame from the lesson in a rounded window, the Reel logo, the group name and the lesson title. See `reel/thumbnail.py`.
