# Bundled fonts — provenance and licences

The Teleprompter ships its typefaces rather than relying on what a machine happens to have
installed (`Theme.qml` names them by role). Redistributing them means carrying their licences,
which is what this folder does. Copyright lines below are the ones embedded in each file's
`name` table — read them out with any font inspector to check this file has not drifted.

| File | Project | Copyright | Licence |
|---|---|---|---|
| `Inter-Variable.ttf` | [Inter](https://github.com/rsms/inter) (Rasmus Andersson) | Copyright 2020 The Inter Project Authors | SIL OFL 1.1 — [`OFL.txt`](OFL.txt) |
| `InstrumentSerif-Regular.ttf`<br>`InstrumentSerif-Italic.ttf` | [Instrument Serif](https://github.com/Instrument/instrument-serif) (Instrument) | Copyright 2022 The Instrument Serif Project Authors | SIL OFL 1.1 — [`OFL.txt`](OFL.txt) |
| `MartianMono-Variable.ttf` | [Martian Mono](https://github.com/evilmartians/mono) (Evil Martians) | Copyright 2020 The Martian Mono Project Authors | SIL OFL 1.1 — [`OFL.txt`](OFL.txt) |
| `lucide.ttf` | [Lucide](https://github.com/lucide-icons/lucide) (Lucide Icons and Contributors) | Copyright 2026 Lucide Icons and Contributors | ISC — [`LICENSE-Lucide-ISC.txt`](LICENSE-Lucide-ISC.txt) |

Every icon the app draws is a glyph from `lucide.ttf`, taken from the `lucide-static` package
unmodified. It ships the full family rather than a subset, so adding an icon is one codepoint in
`Theme.ico` and nothing else — no re-cutting the font, no risk of an icon that renders as an
empty box. `settings_check` asserts every codepoint in that map has a glyph behind it.

Three obligations ride along, all satisfied by keeping this folder intact:

- **OFL 1.1** — the licence and copyright notice must travel with the font, and the fonts may
  not be sold on their own. It also forbids shipping a *modified* font under the original name;
  these are unmodified, so nothing to do unless one is ever subset or patched.
- **ISC** — the copyright notice and permission notice must appear in all copies, which
  [`LICENSE-Lucide-ISC.txt`](LICENSE-Lucide-ISC.txt) carries.
- **MIT** — about a hundred Lucide icons are derived from [Feather](https://github.com/feathericons/feather)
  (Copyright 2013-present Cole Bemis) and carry MIT on top of the ISC. The list and the licence
  text are both in that same file, at the bottom.

`frontend/icons/not-hal-mark.svg` is the project's own mark rather than an icon, and is the only
SVG left in the app. No attribution is owed for it.
