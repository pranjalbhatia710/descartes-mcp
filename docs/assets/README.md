# Sprites for the landing page

The landing page ([`../index.html`](../index.html)) looks for three transparent
PNG sprites here. Until you add them, the page falls back to a hand-drawn SVG
Descartes, so it always renders — dropping in the real pixel art just makes it
come alive.

| filename | where it shows | recommended pose |
| --- | --- | --- |
| `descartes-idle.png` | hero, beside the CRT | seated at the desk with quill (the calm idle pose) |
| `descartes-ponder.png` | the doubt loop | hand on chin with floating “?” (questioning) |
| `descartes-write.png` | the doubt loop | quill to parchment with the “…” speech bubble (writing) |

Tips:
- Use transparent backgrounds (the page composites them over parchment/CRT).
- The CSS already sets `image-rendering: pixelated`, so pixel art stays crisp.
- Roughly square-ish crops work best; the page scales by width.

If you rename or add poses, update the `src="assets/…"` references and the
`faces` array in `index.html`.
