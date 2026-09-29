# memo — brand

**Mark:** a lowercase serif **m** followed by an amber full stop: `m.` It reads as the
end of a thought written down, and as a record light. The wordmark in the app is
`memo.` with the same amber dot.

**Type:** Instrument Serif for display (the wordmark, screen titles); the iOS system
font for everything else.

**Colour** (`apps/mobile/src/ui.ts`):

| Token | Hex | Use |
| --- | --- | --- |
| Deep forest | `#0B241A` | Icon and splash background |
| bg | `#0B1410` | App background: black, tinted green |
| surface / surfaceAlt | `#15221C` / `#1E2F27` | Cards, sheets, inputs |
| text | `#F1ECDF` | Cream, never pure white |
| muted / faint | `#9FAEA4` / `#627368` | Secondary text |
| **amber** (`record`) | `#E2A04A` | The one warm accent. It marks what is live: record, today, a voice session |
| moss (`green`) | `#8FBF96` | Quiet positive states |
| danger | `#E5645A` | Destructive actions only |

**Rules:** one accent, used sparingly. If everything is amber, nothing is live. No
pure black or pure white anywhere.

**Assets:** `scripts/brand_assets.py` regenerates the icon, splash and Android icons
from the mark: `uv run --with pillow python scripts/brand_assets.py`.
