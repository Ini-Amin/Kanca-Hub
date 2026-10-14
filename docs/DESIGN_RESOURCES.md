# Design resources for UI work (curated 2026-10-14)

> Rule (standing): for ANY UI work, load the `antislop-ui` skill (or copy a
> real app). Generic AI-looking UI is a bug. This file is the approved
> inspiration shelf — agents: open 2–3 of these BEFORE drawing anything.

## House style reference

The `GROK BOT MEMORY ENGINEERING` diagram (saved with this note) is the bar for
diagrams: black on white, thin rules, small caps labels, generous whitespace, no
gradients, no stock illustration. For app UI: copy shadcn/Aceternity structure,
not their colors — adapt to the product.
- **diagram-design** (github.com/cathrynlavery/diagram-design, 54k★): 44 editorial
  diagram types as self-contained HTML+SVG, explicitly no-Mermaid-slop. This is
  the pattern library for any diagram agents draw.

## Prompt libraries (describe the design to the agent)

- **jiro.build** — 1200+ AI-ready design prompts (freemium)
- **VibePrompt (vibeprompts.dev)** — ready-made dashboard/landing prompts (freemium)
- **Kinetics (kinetics.colorion.co)** — 150+ motion effects with React code + prompts (free)

## Galleries (feed pixels to the agent)

- **Minimal Gallery (minimal.gallery)** — curated high-end sites (free)
- **Kage (kage.design)** — UI inspiration mapped to prompts (free)
- **Refero Styles (styles.refero.design)** — 2000+ product styles + typography (free)
- **Component Gallery (component.gallery)** — 2600+ ways top design systems solve the same element (free)
- **AppShot Gallery (appshot.gallery)** — real mobile app screenshots (free)
- **footer.design** — footer patterns
- **deck.gallery, godly.design, animos.app** — motion/interaction inspiration
- **transitions.dev, backgrounds.supply** — transitions + backgrounds

## Component systems (copy, don't reinvent)

- **shadcn/ui (ui.shadcn.com)** — gold standard, clean reusable UI (free)
- **Motion Primitives (motion-primitives.com)** — animation/interaction components (free)
- **Uiverse (uiverse.io)** — community UI elements (free)
- **mapcn (mapcn.dev)** — maps, markers, routes, popups (free)
- **MicroKit UI (microkit.co)** — micro-interactions that make UI feel polished (free)
- **Liquid Glass (glass.samasante.com)** — glass refraction effects (free)
- **Aceternity UI (ui.aceternity.com)** — 200+ animated React/Tailwind (freemium)
- **Magic UI (magicui.design)** — modern marketing components (freemium)
- **21st.dev** — component registry with MCP for agents (freemium)
- **UIAble (uiable.com)** — ready-to-use components

## Motion / effects / assets

- **Anime.js (animejs.com)** — lightweight JS animation (free)
- **CSS Text Effects (text-effects.colorion.co)** — text animations (free)
- **Circle Loaders (circleloaders.dominikakissi.com)** — loaders (free)
- **Gradient Buttons (gradientbuttons.colorion.co)** — button styles (free)
- **DESIGNmd (designmd.ai)** — design systems as markdown the agent can read (free)
- **Kitbitz (kitbitz.art)** — 2000+ hand-drawn illustrations (free)
- **3Dicons (3dicons.co)** — free 3D icons (free)

## Workflow for agents

1. `skill antislop-ui` first (core filter).
2. Open 2–3 galleries above, pick ONE reference that fits the product.
3. Steal structure/spacing/type scale from the reference; never default gradients,
   never purple/blue AI-slop palettes, never lorem ipsum.
4. The slider review UI lives in `reports/slider_review/` — it must stay draggable
   and copy a real app.
