# Reading Theme v2 — oxxostudio-inspired Style Guide

Produced with the workflow of [Lucent-Snow/style-extractor](https://github.com/Lucent-Snow/style-extractor)
(Phase 1 evidence → Phase 2 de-productization → Phase 3 semantic tokens → Phase 4 guide).
Applied to the blog/article "reading" theme (`docs/css/reading.css`, `body.theme-reading`).

## Evidence manifest

| Source | Type | Notes |
|--------|------|-------|
| User-supplied screenshot of https://www.oxxostudio.tw/ (homepage, desktop ~1250px) | Primary visual evidence | Header/logo, category nav, article card grid |
| Live site fetch (`www.oxxostudio.tw`), Wayback Machine, reader proxies | **Unavailable** | All blocked by this environment's network policy (proxy CONNECT 403) — `getComputedStyle` capture and motion evidence could not be collected |
| Prior knowledge of the site's design language | Secondary | Used only where consistent with the screenshot |

Gaps: exact hex values are sampled from the screenshot (±small error); hover/motion behavior
is inferred, not measured. Product-specific IA (categories, RSS, author pages) was discarded.

## Extracted design DNA (what makes it "oxxostudio")

1. **Light, friendly canvas** — pale gray page (#ececec-ish), pure-white content cards,
   soft drop shadows, ~8px rounded corners. No dark-tech chrome.
2. **A rainbow of category colors** — every category owns one saturated but soft color
   (red / green / peach / purple / teal / orange). The color repeats on the nav dot,
   the label text, and the card's ribbon.
3. **Ribbon flags** — each card carries a small colored arrow-ribbon at its top-left that
   pokes past the card edge, with a darker fold triangle under the overhang.
4. **Dots on a line** — the category nav is a thin horizontal rule with colored dots
   sitting on it, labels beneath.
5. **Dashed separators** — 1px dashed light-gray rules divide card image/excerpt and
   article sections.
6. **Letter-spaced identity** — logo and tagline ("Stay hungry, stay foolish") in
   generously letter-spaced type; meta rows (clock + date, pencil + author) in muted gray.

## Semantic tokens (implemented on `body.theme-reading`)

| Token | Value | Usage |
|-------|-------|-------|
| `--ox-canvas` | `#eef0f1` | Page background behind card grids (blog index) |
| `--ox-paper` | `#ffffff` | Cards, header, hero |
| `--ox-ink` | `#333333` | Titles, strong text |
| `--ox-ink-2` | `#5f5f5f` | Body/excerpt text |
| `--ox-ink-3` | `#9a9a9a` | Meta text (dates, reading time, eyebrows) |
| `--ox-dash` | `#d8d8d8` | 1px dashed separators |
| `--ox-radius` | `8px` | Card / callout corner radius |
| `--ox-shadow` | `0 1px 4px rgba(0,0,0,.14)` | Resting card shadow |
| `--ox-shadow-hover` | `0 10px 26px rgba(0,0,0,.14)` | Hover card shadow |
| `--accent-cyan` (remap) | `#17789c` | Theme accent: links, active states, h2 bar (AA on white) |

### Category palette (`--cat`, set per card via `data-tags` / per dot via `href`)

| Category | Color | oxxostudio counterpart |
|----------|-------|------------------------|
| 監測概念（暗船/AIS/旁靠/灰色地帶/影子船隊）default | `#3aa4c5` | WEB TECH teal |
| 海底電纜／台灣海纜 | `#35b558` | UI & UX green |
| 參考（詞彙表/海域分區） | `#f2a154` | PHOTO peach |
| 威脅分析 | `#e85d5d` | Creative coral |
| 執法法律 | `#9760b3` | CSS purple |
| 海洋法 | `#5b7ec9` | (harmonized indigo) |
| 方法論 | `#f5a623` | OTHERS orange |
| 學術研究 | `#c8860a` | (kept from existing gold treatment) |

`--cat` drives: ribbon background, card-title hover color, card arrow, series dot fill,
filter-button dot + active text.

## Component rules

- **Card**: white, radius 8, `--ox-shadow`, no border, `overflow: visible` (ribbon overhang);
  hover = lift `-3px` + `--ox-shadow-hover`; title `#333 → var(--cat)` on hover;
  dashed rule above excerpt; watermark numeral and neon top-line removed.
- **Ribbon**: absolute, `top 16px / left −10px`, `var(--cat)` bg, white mono label,
  right arrow tip via border triangle, darker fold under the overhang
  (`color-mix` 60% black, fallback translucent black).
- **Series track**: thin `#e2e2e2` line, filled colored dots (white numerals), labels
  gray → category color on hover.
- **Filter bar**: borderless text buttons, colored 8px dot before each, active/hover =
  category-colored text.
- **Hero (index)**: white, canvas/sweep-line effects off, letter-spaced dark title +
  gray letter-spaced eyebrow (logo/tagline feel), stat numerals in green/teal/orange.
- **Article pages**: white column (unchanged width), `h2` = dashed bottom rule + short
  rounded accent bar; callouts (`story-hook`, `fact-box`, `alert-box`) = tinted paper with
  dashed borders + radius 8; tables = light gray header + dashed row rules; FAQ = white
  rounded cards; stat numbers rotate through the palette; CTA pills fully rounded.

## Motion

Kept from existing theme (entrance fade/rise on cards, IntersectionObserver stagger,
`prefers-reduced-motion` respected). Hover transitions ≤ .25s ease — matches the
screenshot's static-first character; no new keyframes introduced.
