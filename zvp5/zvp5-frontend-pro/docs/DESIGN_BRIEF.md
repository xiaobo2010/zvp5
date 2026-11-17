# ZVMS Pro — Frontend Design Brief

This folder is a clean, standalone frontend prototype for the next ZVMS web experience.
The existing `zvp5-frontend` and the backend are frozen for this phase.

## Purpose

Design and implement **only**:

1. The global site navigation.
2. The marketing-style homepage.
3. A concise design-principles document that the rest of the product can follow.

Everything else (auth, dashboards, AI gateway, admin pages) is intentionally out of scope and
must not be touched.

## Visual direction

- Foundation: the restraint, clarity, and product-first composition of **apple.com**.
- Premium, quiet, confident. Generous whitespace. Large editorial typography.
- **Light theme only** for this pass.
- Brand palette: ZVMS sky blue, near-black, white, and neutral grays. Logo/brand color may be used
  as a rare accent, not as a wash.
- Materials: liquid glass used with discipline — top navigation, floating controls, one hero
  surface. Glass needs a real light source, edge highlight, and soft shadow; do not put translucent
  panels inside one another.
- Light: one believable light source per scene. Soft bloom, low-frequency gradients, subtle grain
  if useful. Avoid purple “AI SaaS” gradients, neon glows, and decorative blobs.
- Shape: restrained Apple-like radii; avoid making every element a pill or a rounded card.
- Density: spacious on desktop; do not shrink the page into a generic admin dashboard.

## Motion

- “Q弹” but controlled: spring-like scale/translate on pressable surfaces, gentle parallax and
  reveal on scroll.
- Use non-linear easing. Prefer transform/opacity. Avoid layout-triggering animation.
- Respect `prefers-reduced-motion`.
- Performance matters: no animation loop that runs forever, no heavy animation dependency, no
  large raster assets. Target smooth 60fps on a mid-range laptop and mobile.

## Navigation

- Sticky, liquid-glass top bar that stays legible over the hero and white sections.
- Desktop: compact wordmark, five or six text links, one primary CTA.
- Mobile: accessible menu with focus trapping/escape behavior, no horizontal overflow.
- Links are anchors on the homepage for this prototype:
  首页, 志愿活动, 志愿时长, 组织, 关于.
- CTA: 开始使用.
- Provide clear hover, focus-visible, and active states.

## Homepage

Create a complete, responsive homepage with real Chinese copy (not lorem ipsum) for a campus
volunteer platform. Suitable sections:

- Hero: one strong claim, short supporting line, primary + secondary CTA, one beautiful product
  visual made from HTML/CSS (not a screenshot). Use a glass surface and a single light bloom.
- Proof/impact strip: 3–4 meaningful numbers with restrained labels.
- Product story: 2–3 alternating editorial sections showing how discovering, joining, and
  recording service works. Build the product UI mockups in HTML/CSS.
- Activity cards or an organization/quote section with realistic Chinese content.
- Closing CTA and a quiet footer.

The page must feel like a product launch page, not a dashboard. Do not use stock photos; create
visual interest with typography, light, glass, and CSS-composed UI fragments.

## Responsive requirements

- Verify at 1440, 1024, 768, and 390 CSS px.
- No horizontal overflow at any width.
- Nav must remain usable at 390px; hero type must not clip.
- Touch targets >= 44px where practical.
- Content max-width and section rhythm should feel intentional on 2K/4K screens.

## Accessibility

- Semantic landmarks, one `h1`, logical heading order.
- Visible `:focus-visible` states.
- Sufficient contrast for body text and glass surfaces.
- Menu is keyboard operable and `aria-expanded` is correct.
- Decorative light effects are hidden from assistive technology.

## Technical constraints

- Vue 3 + TypeScript + plain CSS. No new UI framework, no animation library, no icon package.
- Keep the prototype self-contained in `zvp5/zvp5-frontend-pro`.
- Use the existing scaffold (`src/App.vue`, `src/router/index.ts`) and replace the placeholder
  `SiteNav.vue` and `HomeView.vue`.
- You may add components under `src/components/home/` and style files under `src/styles/`.
- Avoid images/fonts that require network downloads. System font stack only.
- Use CSS custom properties for tokens; define them in `src/styles/tokens.css` and import from
  `base.css` (or keep one token file if cleaner).
- Do not install packages or run full test suites. A lightweight `vite` preview is optional.
- Do not edit anything outside this folder.

## Deliverables

- `src/components/SiteNav.vue` — final navigation.
- `src/views/HomeView.vue` — final homepage, composed from local components as needed.
- `src/styles/tokens.css` — design tokens (color, type, space, radius, shadow, glass, motion).
- `src/styles/base.css` — global reset, typography, light-theme canvas.
- `docs/DESIGN_PRINCIPLES.md` — concise Chinese design guidelines with English technical terms:
  visual principles, type scale, spacing, radii, glass recipe, light rules, motion/easing,
  performance budget, accessibility, do/don't examples.
- Optional: `src/components/home/*.vue` and `src/styles/home.css`.

## Definition of done

- The page runs with the existing scaffold and looks intentional at all four widths.
- The navigation and homepage share one coherent material/light system.
- The design doc is specific enough that another engineer can build a second page without guessing.
- No placeholder copy, no broken links, no console errors, no horizontal scroll.
