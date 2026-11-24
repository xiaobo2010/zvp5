<template>
  <div class="card-brackets" aria-hidden="true">
    <svg class="bracket bracket-tl" width="20" height="20" viewBox="0 0 20 20">
      <path d="M20 0H4C1.8 0 0 1.8 0 4v16" stroke="currentColor" fill="none" />
    </svg>
    <svg class="bracket bracket-tr" width="20" height="20" viewBox="0 0 20 20">
      <path d="M0 0h16c2.2 0 4 1.8 4 4v16" stroke="currentColor" fill="none" />
    </svg>
    <svg class="bracket bracket-bl" width="20" height="20" viewBox="0 0 20 20">
      <path d="M20 20H4c-2.2 0-4-1.8-4-4V0" stroke="currentColor" fill="none" />
    </svg>
    <svg class="bracket bracket-br" width="20" height="20" viewBox="0 0 20 20">
      <path d="M0 20h16c2.2 0 4-1.8 4-4V0" stroke="currentColor" fill="none" />
    </svg>
  </div>
</template>

<style scoped>
.card-brackets {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.bracket {
  position: absolute;
  width: 20px;
  height: 20px;
  color: rgb(169 211 255 / 0.3);
  stroke-width: 1;
}

.bracket-tl {
  top: 12px;
  left: 12px;
}

.bracket-tr {
  top: 12px;
  right: 12px;
}

.bracket-bl {
  bottom: 12px;
  left: 12px;
}

.bracket-br {
  bottom: 12px;
  right: 12px;
}
</style>
