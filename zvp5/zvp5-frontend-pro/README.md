# ZVMS Frontend Pro

Standalone design-led prototype for the next ZVMS frontend. This pass covers the global
navigation, homepage, and design principles only.

```bash
# local prototype (reuses the sibling frontend's installed dependencies)
ln -s ../zvp5-frontend/node_modules node_modules   # once, local only
pnpm dev                                            # http://localhost:5273
pnpm build
```

Design brief: `docs/DESIGN_BRIEF.md`
Design principles: `docs/DESIGN_PRINCIPLES.md`

The existing `zvp5-frontend` is not modified by this prototype.
