const fs = require("node:fs");
const path = require("node:path");

const [distDir, staticBase, pageBase] = process.argv.slice(2);
if (!distDir || !staticBase || !pageBase) {
  throw new Error(
    "usage: verify-static-base.cjs <dist-dir> <static-base> <page-base>",
  );
}

const html = fs.readFileSync(path.join(distDir, "index.html"), "utf8");
for (const expected of [
  `src="${staticBase}assets/`,
  `href="${staticBase}favicon-32x32.png"`,
  `href="${staticBase}apple-touch-icon.png"`,
  `href="${staticBase}safari-pinned-tab.svg"`,
  `href="${staticBase}manifest.webmanifest"`,
]) {
  if (!html.includes(expected)) {
    throw new Error(`missing ${expected} in dist/index.html`);
  }
}

if (/(?:src|href)="\/assets\//.test(html)) {
  throw new Error(
    "dist/index.html still contains same-origin /assets references",
  );
}

const manifest = JSON.parse(
  fs.readFileSync(path.join(distDir, "manifest.webmanifest"), "utf8"),
);
if (
  staticBase !== "/" &&
  (manifest.icons || []).some(
    (icon) => !String(icon.src).startsWith(staticBase),
  )
) {
  throw new Error("manifest icons were not moved to VITE_STATIC_BASE");
}
if (manifest.start_url !== pageBase || manifest.scope !== pageBase) {
  throw new Error(
    "manifest start_url/scope were not anchored to VITE_PAGE_BASE",
  );
}

const sw = fs.readFileSync(path.join(distDir, "sw.js"), "utf8");
if (staticBase !== "/" && !sw.includes(`${staticBase}assets/`)) {
  throw new Error(
    "VITE_STATIC_BASE was not applied to the service worker precache",
  );
}
if (staticBase !== "/") {
  const relativePrecacheUrls = [...sw.matchAll(/url:"([^"]+)"/g)]
    .map((match) => match[1])
    .filter((url) => !url.startsWith("https://"));
  if (relativePrecacheUrls.length > 0) {
    throw new Error(
      `service worker still precaches same-origin files: ${relativePrecacheUrls.join(", ")}`,
    );
  }
}
