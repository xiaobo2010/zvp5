# AI Gateway UI Visual Refactor Draft

> 2026-11-16 已按本稿落地：AIHome 改为扁平页头 + 下划线分区导航；ZAiChat 设置卡加入「流式输出」开关（按账号存 localStorage，默认开启，多 worker 503 自动回退轮询）；主操作按钮套用 `.yg-btn`/`.yg-icon-btn`。本文保留为设计依据，细节以代码为准。

## Current Problems

1. **Button inconsistency**: All ZAiChat actions use Element Plus `ElButton` defaults (rounded, colored fills), ignoring the project's `.yg-btn` system (12px radius ≈ 70% of typical, flat with shadows, sky blue + black/white).
2. **Hierarchy unclear**: Primary/secondary distinction blurs—send/stop/refresh/compact all share similar visual weight.
3. **Settings card density**: Desktop mode packs model picker + thinking + context meter + actions into a single horizontal row that wraps awkwardly at mid-size viewports.
4. **Missing streaming toggle**: Backend supports SSE streaming (`GET /api/ai/runs/{run_id}/events`); frontend needs a clear, tasteful placement for "流式输出" toggle + short hint in settings card.
5. **Tab navigation feel**: Compact header + pill nav works, but the glass pill container could be more refined; desktop `ElSegmented` is functional but lacks personality.

## Proposed Layout

### Desktop (≥1024px, horizontal)

**Header Shell**:
- Replace `ElPageHeader` with custom flat header: left-aligned title + description, right-aligned `.yg-btn` icon-only actions (pending proposals badge if >0, settings gear).
- Tab bar below: horizontal pill strip with icons + labels, active pill gets sky blue soft background + shadow; visually lighter than current `ElSegmented` (no thick border, no vertical segments).

**Chat Settings Card** (摊开态):
- Split into **two logical rows**:
  1. **Selection row**: Model picker (with lock icon if locked) + thinking level (4-segment) + **streaming toggle** (switch + "流式输出" label + tooltip hint).
  2. **Status row**: Context meter (progress + usage text + auto-compact hint) + compact button (text, small).
- Streaming toggle sits after thinking picker, visually grouped with "what model does" rather than "how full is context". Switch uses sky blue for "on" state.

### Narrow/Mobile (<1024px or vertical)

**Compact Header + Pill Nav** (keep existing pattern):
- Keep current `.ai-head` + `.ai-tabs` glass pill nav; refine pill active state to match desktop (soft blue bg + sm shadow, no thick border).
- Settings card remains collapsible; folded summary adds streaming status: "模型：… · 思考：… · 流式：开/关".

### Streaming Toggle Details

- **Component**: `ElSwitch` (Element Plus native, accessible).
- **Label**: "流式输出" (inline, `font-size: sm`, `color: text-secondary`).
- **Hint**: Tooltip or `title` attribute: "实时显示每轮回复，避免长时间等待" (15 chars limit for inline, full text in tooltip).
- **Position**: In settings row 1, after thinking picker, before context separator `<span class="ai-chat__settings-sep" />`.
- **State**: Disabled while a run is active; preference is stored per account in `localStorage` (`zvms.ai.streaming.<user_oid>`) and defaults to on. If the deployment returns 503 `AI_RUN_EVENTS_UNSUPPORTED`, the store falls back to polling and the settings card shows a hint.

## Tab/Nav Treatment

**Desktop Tabs** (replace `ElPageHeader` + `ElSegmented`):
- Flat horizontal bar, white surface with bottom border (`1px solid --yg-border-default`).
- Each tab: inline-flex icon + label, `padding: 0 var(--yg-space-4)`, `height: 44px`, `border-radius: 0`, `border-bottom: 2px solid transparent`.
- Active tab: `color: --yg-color-primary`, `border-bottom-color: --yg-color-primary`, `font-weight: medium`.
- Hover: `background-color: --yg-bg-hover`.

**Compact Pill Nav** (refine existing `.ai-tabs`):
- Keep glass backdrop, horizontal scroll.
- Active pill: `background: --yg-color-primary-soft`, `box-shadow: --yg-shadow-sm`, `color: --yg-color-primary`, `font-weight: medium` (already in place; ensure sky blue not bright blue).
- Inactive hover: `background: --yg-bg-hover`.

## Button Hierarchy

### Send/Stop (composer bottom right)
- **Send**: `.yg-btn.yg-btn--primary` (sky blue solid, white text, sm shadow). Icon: paper-plane / arrow-right.
- **Stop**: `.yg-btn.yg-btn--danger` (red solid, white text, sm shadow). Icon: VideoPause.

### Settings Card Actions
- **Refresh models**: `.yg-btn.yg-btn--text` (sky blue text, underline on hover, no bg). Size small.
- **Compact context**: `.yg-btn.yg-btn--text` (sky blue text). Size small.
- **Expand/fold settings** (short viewport): `.yg-btn.yg-btn--text`. Size small.

### Session List
- **New conversation**: `.yg-btn.yg-btn--primary` (full width, block, lg size on mobile). Icon: Plus.
- **Rename/archive/delete**: `.yg-icon-btn` (ghost, size md). Icon: Edit / Archive / Delete.

### Proposal Cards
- **Confirm**: `.yg-btn.yg-btn--primary`. Size default.
- **Reject**: `.yg-btn.yg-btn--secondary` (white + border). Size default.
- **Retry**: `.yg-btn.yg-btn--ghost`. Size small.

### Top Actions (desktop header right)
- **Pending proposals badge**: `.yg-icon-btn.yg-icon-btn--primary` if count > 0, else `.yg-icon-btn--ghost`. Icon: Bell / Notification with badge dot.
- **Settings**: `.yg-icon-btn--ghost`. Icon: Setting.

## Exact CSS Token Usage

**Colors**:
- Primary action bg: `--yg-color-primary-strong` (sky-700, white text 5.93:1).
- Primary hover: `--yg-color-primary-strong-hover` (sky-800).
- Primary active: `--yg-color-primary-strong-active` (sky-900).
- Secondary/ghost hover: `--yg-bg-hover` (sky-100, 1.28:1 contrast).
- Active pill/tab bg: `--yg-color-primary-soft` (sky-50).
- Text link: `--yg-text-link` (sky-700).
- Danger: `--yg-color-danger-strong` (red-700).
- Border: `--yg-border-default`, `--yg-border-strong`.
- Surface: `--yg-bg-surface` (white), `--yg-bg-page` (#F5F5F7).

**Spacing**:
- Button gap: `--yg-space-2` (8px).
- Card internal gap: `--yg-space-4` (16px).
- Settings row gap: `--yg-space-3` (12px).
- Tab padding-inline: `--yg-space-4` (16px).

**Radius**:
- Buttons: `--yg-radius-md` (12px) + `corner-shape: squircle` in supporting browsers.
- Cards: `--yg-radius-lg` (16px).
- Pill nav: `--yg-radius-pill` (999px).
- Tabs: 0 (flat bottom border only).

**Shadows**:
- Primary/danger buttons: `--yg-shadow-sm`.
- Active pills: `--yg-shadow-sm`.
- Cards: `--yg-shadow-md` or `shadow="never"` (per existing AIHome pattern).
- Focus ring: `--yg-focus-ring` (2px sky-500 + 2px white inner).

**Typography**:
- Button: `font-size: --yg-font-size-md` (14px), `font-weight: --yg-font-weight-medium` (500).
- Small button: `font-size: --yg-font-size-sm` (13px).
- Settings label: `font-size: --yg-font-size-xs` (12px), `color: --yg-text-tertiary`.
- Tab label: `font-size: --yg-font-size-md`, `line-height: --yg-line-height-snug`.

## Responsive Behavior

**Desktop → Tablet (1024px → 768px)**:
- Settings card row 1 wraps: model + thinking on first line, streaming toggle + context on second.
- Tab strip remains horizontal; overflowing tabs scroll (`::-webkit-scrollbar { display: none }`).

**Tablet → Mobile (<768px)**:
- Compact header + pill nav already in use.
- Settings card auto-folds below 760px viewport height (existing `isShortViewport` logic).
- Touch targets: all buttons ≥44px (`.yg-btn--sm` gets `min-height: 44px` on mobile per existing buttons.css).

**Narrow Viewport (<1024px or vertical)**:
- Session drawer (existing `ElDrawer` ltr).
- Send/stop buttons remain icon + text (not icon-only) for clarity.

## Accessibility Notes

- All icon-only buttons get explicit `aria-label` (e.g., "刷新模型列表").
- Streaming toggle: `ElSwitch` has native ARIA roles; label associates with switch via `for` or wrapping.
- Tab bar: `role="tablist"`, each tab `role="tab"`, `aria-selected="true/false"`.
- Focus ring visible on all interactive elements (`:focus-visible` → `--yg-focus-ring`).
- Color contrast: sky-700 on white = 5.93:1 (AA), red-700 on white = 4.54:1 (AA for large text / UI components per WCAG 1.4.3 relaxed for non-text).
- Disabled state text: `--yg-text-disabled-control` (#8A8A8F) on `--yg-bg-surface-sunken` = 3.08:1 (sufficient for "this is disabled" signal).

## Implementation Notes for Orchestrator

### Files to Modify

**AIHome.vue**:
- Desktop: replace `<ElPageHeader>` + `<ElSegmented>` with custom `.ai-gateway-header` + `.ai-gateway-tabs` (flat tabs, active = bottom border).
- Compact: refine `.ai-tabs__pill.is-active` to use `--yg-color-primary-soft` + `--yg-shadow-sm` (already mostly in place; confirm sky blue not bright blue).

**ZAiChat.vue**:
- Settings card header: keep `<template #header>` structure; buttons switch from `<ElButton>` to `.yg-btn` classes.
- Settings body row 1: add streaming toggle after `ZAiModelThinkingPicker`, before separator `<span class="ai-chat__settings-sep" />`.
  ```vue
  <ElSwitch
    v-model="streamEnabled"
    :disabled="!store.current || modelsLoading"
    size="small"
    :active-color="'var(--yg-color-primary)'"
  />
  <span class="ai-chat__settings-label">流式输出</span>
  <ElIcon class="ai-chat__settings-hint-icon" :title="t('ai.chat.streamHint')">
    <QuestionFilled />
  </ElIcon>
  ```
- Folded summary: append "· 流式：开/关".
- Composer buttons: replace `<ElButton type="primary">` with `<button class="yg-btn yg-btn--primary">` (send), `<button class="yg-btn yg-btn--danger">` (stop).
- Session list "New" button: `.yg-btn.yg-btn--primary.yg-btn--block`.

**ZAiModelThinkingPicker.vue**:
- Already uses `ElSelect` + `ElSegmented`; leave picker internals unchanged; consuming components wrap it in settings row and handle streaming separately.

**Styles**:
- `@/assets/styles/yg/components/buttons.css` is already imported through `assets/styles/yg/main.css`; no new global import is needed.
- Add scoped utility classes in `ZAiChat.vue` for settings row layout:
  ```css
  .ai-chat__settings-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--yg-space-3);
  }
  .ai-chat__settings-sep {
    width: 1px;
    height: 20px;
    background-color: var(--yg-border-default);
    margin-inline: var(--yg-space-2);
  }
  ```

### Markup Pattern Example (Settings Card Body)

```vue
<div class="ai-chat__settings-body">
  <!-- Row 1: Selection -->
  <div class="ai-chat__settings-row">
    <ZAiModelThinkingPicker ... />

    <!-- Streaming toggle -->
    <ElSwitch v-model="streamEnabled" size="small" :disabled="..." />
    <label class="ai-chat__settings-label">流式输出</label>
    <ElTooltip :content="t('ai.chat.streamHint')" placement="top">
      <ElIcon class="ai-chat__settings-hint-icon"><QuestionFilled /></ElIcon>
    </ElTooltip>

    <span class="ai-chat__settings-sep" aria-hidden="true" />

    <!-- Context meter -->
    <div class="ai-chat__context">...</div>
    <button class="yg-btn yg-btn--text yg-btn--sm" @click="compactContext">
      {{ t('ai.chat.sessions.context.compact') }}
    </button>
  </div>
</div>
```

### CSS Classes to Replace

| Current Element | Current Class | New Class |
|---|---|---|
| Send button (composer) | `<ElButton type="primary">` | `<button class="yg-btn yg-btn--primary">` |
| Stop button | `<ElButton type="danger">` | `<button class="yg-btn yg-btn--danger">` |
| Refresh models | `<ElButton text>` | `<button class="yg-btn yg-btn--text yg-btn--sm">` |
| Compact context | `<ElButton text size="small">` | `<button class="yg-btn yg-btn--text yg-btn--sm">` |
| New conversation | `<ElButton type="primary">` | `<button class="yg-btn yg-btn--primary yg-btn--block">` |
| Session action icons | `<ElButton circle>` or icon-only | `<button class="yg-icon-btn yg-icon-btn--ghost">` |

**Note**: Keep `ElButton` for proposal confirm/reject in `ZAiProposalCard.vue` for now (out of this draft's scope); orchestrator may refactor those in a second pass.
