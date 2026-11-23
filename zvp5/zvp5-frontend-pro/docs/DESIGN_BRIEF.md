# ZVMS Pro — Signed-in Home (authoritative brief)

This folder is the standalone next frontend. The existing `zvp5-frontend` and backend are frozen.
This pass is a **complete rewrite of the signed-in home**, not a marketing page and not the current
next-activity dashboard.

## Functional scope (must match the canary home)

1. **Signed-in navigation**
   - ZVMS wordmark; 首页 / 活动 / 时长 / 组织.
   - Search, notifications, avatar/account.
   - Sticky, slim, and **liquid glass**. Every nav icon button is also glass.
2. **Personal information**
   - Avatar, name, student number, groups/roles, edit-profile action.
   - Reads like an account/identity surface, not a profile form.
3. **Three volunteer-hour categories**
   - 校内时长 (`on-campus`), 校外时长 (`off-campus`), 社会实践 (`social-practice`).
   - Show actual value, standard target, progress, and any complementary conversion.
   - Canary standards: 校内 25h, 校外 15h, 社会实践 18h.
   - Conversion rules: off-campus > 15h contributes `round((off - 15) / 2)` to on-campus;
     on-campus > 25h contributes `round((on - 25) / 3)` to off-campus. Total is the raw sum.
   - Include a total-record figure and a “明细 / 刷新” action.
4. **Work list / record list**
   - Canary home shows a review/work list. Implement it without a table: a clean glass list of
     pending activities with name, creator/organization, date, and status.
   - Include a compact recent-records list (activity, date, duration, confirmed/pending) using the
     same list language. No `<table>`.
   - If a role filter is useful, use a glass segmented control; do not invent a new admin console.

## Visual direction

- Existing light Apple-like direction is acceptable, but the composition must now be a real app
  home: identity → hours → work list, with one clear reading order.
- Canvas `#f5f5f7`, white/opaque surfaces for quiet content, one sky-blue accent.
- **Liquid glass** is mandatory for:
  - the sticky navigation;
  - every button/icon button;
  - the three-hour panel and the list panels (or a convincing frosted fallback if glass would
    hurt legibility/performance).
- Glass recipe for light surfaces:
  - translucent white gradient tint, not a gray slab;
  - `backdrop-filter: blur(14px) saturate(180%) brightness(1.04)`;
  - 1px white specular border, inset top highlight, soft outer shadow;
  - a single diagonal sheen; no rainbow gradients;
  - fallback via `@supports` and a solid translucent surface.
- Keep glass restrained: do not blur every row. Lists use a glass panel with transparent rows and
  hairline separators; buttons get their own specular edge.
- Apple-like geometry: 20–28px panel radii, 12–16px inner rows, hairline borders, no heavy shadows.
- Typography: system stack; 56–72px greeting; 17–19px body; 600 headings; tight tracking only on
  large text. Chinese headings must not split a word or leave an orphan character.
- Inline SVG line icons only. No icon package, images, web fonts, or animation libraries.

## Layout

- Desktop (≥1024): 1180–1220px max width; nav aligned to the same grid.
  - Header: greeting/date.
  - Identity row: personal card (7 cols) + total-hours summary (5 cols), or identity integrated
    into the header with total-hours as the anchor.
  - Time panel: one glass surface, three columns/cells with standards and progress.
  - Work list: 7/5 split, pending activities + recent records; both glass panels.
- Tablet: two columns where useful; time cells 1+2 or 3 across if they fit.
- Mobile: single column; identity first, then hours, then lists; nav usable; no horizontal overflow.

## Motion

- One staggered entrance, spring press/hover; transform/opacity only.
- Buttons: scale 0.98 on press, 1.01 on hover; sheen moves on hover only.
- `prefers-reduced-motion`: no transforms/animations; keep the final state.
- Performance: blur 8–18px; no animated `backdrop-filter`; no glass on every scrolling row.

## Accessibility

- Semantic landmarks; one `h1`; buttons vs links correct.
- Visible focus; contrast checked on the actual glass background.
- Mobile menu: keyboard operable, Escape closes, `aria-expanded` correct.
- Progress bars have accessible labels/values.

## Technical constraints

- Vue 3 + TypeScript + plain CSS; no new dependencies.
- Keep the app self-contained in `zvp5/zvp5-frontend-pro`; `src/App.vue` and router stay working.
- Static prototype data is fine, but the labels/values must be coherent and use the canary rules.
- Do not edit outside this folder; do not commit.
- Verify with one lightweight Vite preview; `vue-tsc` must pass.

## Definition of done

- A signed-in user can see who they are, all three hour categories with standards/progress, and the
  work/record lists without a table.
- Nav and every button read as liquid glass on a light page.
- No marketing remnants, no next-activity sales card, no dead controls.
- 1440 / 1024 / 768 / 390: no horizontal overflow; Chinese headings intact.
