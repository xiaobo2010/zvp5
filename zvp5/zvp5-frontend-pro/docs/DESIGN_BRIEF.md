# ZVMS Pro — Authenticated Home Design Brief

This folder is a clean, standalone frontend prototype for the next ZVMS web experience.
The existing `zvp5-frontend` and the backend are frozen for this phase.

## Target

Design the **screen a signed-in volunteer sees immediately after login** — not a marketing
landing page. It is the product home: a calm, premium daily overview that helps someone decide
what to do next.

Only this pass is in scope:

1. The signed-in global navigation.
2. The signed-in home dashboard.
3. The design-principles document, updated for an app surface rather than a landing page.

Everything else (auth, activity detail, AI gateway, admin pages) is out of scope.

## Visual direction

- Foundation: Apple’s account/product surfaces — Apple ID, iCloud, Wallet, Health, and the
  restraint of apple.com. Not a generic SaaS dashboard.
- Light theme only. Canvas `#f5f5f7`-ish, white surfaces, near-black type, one sky-blue accent.
- Product-first composition: one clear focal object (the next activity), then quiet supporting
  detail. Large editorial type; precise alignment; generous but purposeful whitespace.
- Liquid glass only where it has a job: the global nav, one hero “pass” surface, small floating
  controls. Each glass surface needs a real light source, an edge highlight, and a soft shadow.
  Never nest translucent surfaces.
- One believable light source per scene. Soft bloom, low-frequency gradients, subtle grain only
  if it helps. No purple gradients, neon, decorative blobs, or repeated glow.
- Apple-like restraint: 22–30px card radii, hairline borders, barely-there shadows. Do not turn
  every button into a pill, and do not use colored rounded-square icons everywhere.
- Typography: system SF-like stack, large 56–72px greeting, 17–19px body, 600 weight for most
  headings, tight tracking only on very large text. Let whitespace create hierarchy.
- Icons: hand-authored inline SVG line icons, 1.5–1.8px strokes. No icon package.

## Navigation (signed in)

- Slim 52–60px sticky glass bar, content aligned to the same grid as the page.
- Left: ZVMS wordmark.
- Center/left links: 首页, 活动, 时长, 组织.
- Right: search, notifications, avatar/menu. No “开始使用” marketing CTA.
- Desktop hover/focus states are quiet. Mobile: accessible compact menu; no horizontal overflow.
- The active item is clear but not loud. The nav should read as a product chrome, not a website
  header.

## Home content

Use realistic Chinese content and static prototype data. Suggested composition:

1. Greeting block: “下午好，知远。” plus a quiet date and one line of context (“这周你有 2 场志愿安排”).
2. Focal “next activity” pass: liquid-glass/ticket-like surface, activity title, date/time,
   location, role, status, and one primary action. This is the visual anchor.
3. A compact stats row: 累计时长, 本月新增, 已完成活动, 待确认记录. Large numerals, tiny labels.
4. Bento-style supporting grid:
   - My hours / recent trend (CSS-only chart, no library)
   - To-do or pending confirmations
   - Recommended activities (2 cards max)
   - Organization update or certificate shortcut
   - Quick actions: 找活动, 记录时长, 我的证书, 联系组织
5. A quiet footer or end-of-page utility row. No marketing CTA section.

The page should feel useful at a glance: the next commitment, current standing, and one obvious
next action.

## Motion

- Controlled “q弹” feel: spring-eased press and card hover, one staggered entrance on first load.
- Non-linear easing; transform/opacity only; no animated layout properties.
- No infinite loops. Respect `prefers-reduced-motion`.
- Performance: plain CSS and tiny Vue state, no animation library, no heavy raster assets.

## Responsive

- Verify 1440, 1024, 768, and 390 CSS px.
- Desktop: 12-column bento with a strong focal card. Tablet: 2 columns. Mobile: single column,
  nav remains usable, focal card appears before secondary analytics.
- No horizontal overflow; Chinese headings must never leave an orphan character or split a word
  at a line break.
- Touch targets >= 44px where practical.

## Accessibility

- Semantic landmarks, one `h1`, logical heading order.
- Visible `:focus-visible`; contrast for body and glass surfaces.
- Mobile menu keyboard operable, `aria-expanded` correct, Escape closes.
- Decorative light effects hidden from assistive technology.

## Technical constraints

- Vue 3 + TypeScript + plain CSS. No new dependencies, images, web fonts, or icon libraries.
- Keep the prototype self-contained in `zvp5/zvp5-frontend-pro`.
- Replace `src/components/SiteNav.vue`, `src/views/HomeView.vue`, and the relevant tokens/styles.
- `src/App.vue` and the router scaffold stay working; only the home route is needed.
- Do not edit anything outside this folder. Do not commit.
- No full test suites; one lightweight Vite preview/type-check is enough.

## Deliverables

- `src/components/SiteNav.vue` — signed-in navigation.
- `src/views/HomeView.vue` — signed-in home dashboard, local components as needed.
- `src/styles/tokens.css` / `src/styles/base.css` — app-surface tokens and canvas.
- `docs/DESIGN_PRINCIPLES.md` — Chinese design guidelines with English technical terms.
- Optional: `src/components/home/*.vue`.

## Definition of done

- A signed-in volunteer can understand their next activity, hours, and pending items at a glance.
- Apple-like restraint and material quality are visible at 1440 and 1024, not just at mobile.
- No marketing hero/CTA remnants, no placeholder copy, no broken controls, no console errors.
