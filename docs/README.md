# SLATE v2 — Documentation Index

Start here: **[`ORCHESTRATION.md`](./ORCHESTRATION.md)** — how to schedule and
run the component builds (waves, session mechanics, merge discipline,
escalation).

Then: **[`CONTRACTS.md`](./CONTRACTS.md)** — the binding shared interface
every component doc below depends on (event vocabulary, data registry
namespaces, directory layout, architectural rules). Every session should
read this in full before touching code.

Background: **[`SLATE_REDESIGN_SPEC.md`](./SLATE_REDESIGN_SPEC.md)** — the
original design document this whole rebuild derives from. The component
docs below are the actionable breakdown of it; read this if you want the
narrative/rationale behind a decision a component doc states as given.

## Component specs (`components/`)

| # | Doc | Wave | Depends on |
|---|---|---|---|
| 00 | [`00-foundation-core.md`](./components/00-foundation-core.md) | 0 | — |
| 01 | [`01-stats-combat.md`](./components/01-stats-combat.md) | 1 | 00 |
| 02 | [`02-ai-system.md`](./components/02-ai-system.md) | 1 | 00 |
| 03 | [`03-spells-status.md`](./components/03-spells-status.md) | 1 | 00 |
| 04 | [`04-inventory-items-loot.md`](./components/04-inventory-items-loot.md) | 1 | 00 |
| 05 | [`05-progression-vision.md`](./components/05-progression-vision.md) | 1 | 00 |
| 06 | [`06-worldgen-campaign.md`](./components/06-worldgen-campaign.md) | 1 | 00 |
| 07 | [`07-input-renderer-audio.md`](./components/07-input-renderer-audio.md) | 1 | 00 |
| 08 | [`08-ui-runtime.md`](./components/08-ui-runtime.md) | 1 | 00 |
| 09 | [`09-animation-vfx.md`](./components/09-animation-vfx.md) | 1 | 00 |
| 10 | [`10-lua-scripting-layer.md`](./components/10-lua-scripting-layer.md) | 1 | 00 |
| 11 | [`11-npc-dialog-shop-content.md`](./components/11-npc-dialog-shop-content.md) | 2 | 10, 08 |
| 12 | [`12-modding-archive-system.md`](./components/12-modding-archive-system.md) | 1 | 00 |
| 13 | [`13-editor-core-authoring.md`](./components/13-editor-core-authoring.md) | 1 | 00 |
| 14 | [`14-editor-visual-quest-dialog.md`](./components/14-editor-visual-quest-dialog.md) | 2 | 10, 13 |
| 15 | [`15-integration-verification.md`](./components/15-integration-verification.md) | 3 | everything |

See `ORCHESTRATION.md` §3 for the full wave graph and §2 for how to launch
a session against any one of these.
