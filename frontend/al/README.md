# Gem — mascot sprite kit · handoff (v3)

Gem is the character that represents not-hal while she works: a pixel sprite
derived from the not-hal mark itself (the 3/4 disc with the hard square corner).
One **state** per assistant state; each state holds several **clips** — a base
loop, plus enters, exits, one-shot actions and holds.

Nothing here is hand-drawn frame by frame in this folder. Every frame is
generated and edited in the design source, so the app and the design sheet stay
in sync — see **Regenerating** at the end.

> **v3 replaces v2 in place.** The cell is still 32 × 32 and the loader API is
> unchanged, so this is a drop-in swap for the four asset files plus the loader.
> Two things changed that the app should know about: the **eyes are now 2 px
> wide**, and the idle script is **two-tier** (filler vs gags), which needs the
> updated loader in this folder. v2's files are archived in `v2-archive/`.

> ### not-hal runs the 26 × 26 build (Option B)
>
> The files here are Design's `26/` export, not the 32 one this README describes.
> Same 497 frames, same clip and state names, same script, same palette — the
> cell is cropped tighter, so Gem is 54 % of the icon width instead of 44 % and
> renders 1.23× larger at a given box. Read the size figures below as:
>
> | this README says | not-hal ships |
> | --- | --- |
> | cell 32 × 32 | **26 × 26** |
> | atlas 2240 × 768 | **1820 × 624** |
> | 4× atlas 8960 × 3072 | **7280 × 2496** |
> | integer scales 32 / 64 / 96 | **26 / 52 / 78** |
> | `anchor` x6 y6 w20 h20 | **x0 y0 w26 h26** (the cell is its own tight box) |
> | `body` x9 y11, eyeCols 12–13 / 18–19 | **x6 y6, eyeCols 8–9 / 15–16** |
>
> One frame is clipped by the crop and **owed a pass in the sprite lab**:
> `needs-permission/granted` f5, the falling lock, loses 5 px off the bottom.
>
> `gem_sprites.py` / `gem_sprites.rs` are deliberately not vendored —
> `frontend/al.py` is this app's renderer (Qt, no Pillow) and ports the
> two-tier script from them. §7's `recrop_26.py` is superseded by Design's own
> `26/` export and has been removed.
>
> **not-hal mutes two idle fidgets** (Thomas): `look-around` (a filler) and `jump`
> (a gag) never fire. The frames and their script weights still ship here — the
> skip lives in `al.py`'s `MUTED`, because the next export overwrites this
> folder. Read §3's tables as five gags and one filler.
>
> **`working` is the typewriter** as of v2.3, replacing the laptop:
> `typewriter` / `typewriter-in` / `typewriter-out`. Nothing else moved — the
> other eight states are frame-identical to the previous kit.

---

## 1. What is in this folder

| file | purpose |
| --- | --- |
| `al-sprites.json` | **Source of truth.** All states, clips, frames, timing, palette. |
| `al-atlas.png` | 2240 × 768 colour atlas. 32 px cells, one row per clip. |
| `al-atlas-4x.png` | Same atlas at 4× (8960 × 3072), for docs and store art. |
| `al-tray-template.png` | Monochrome template: white body, eyes punched to transparent. |
| `gem_sprites.py` | Pillow loader + `GemPlayer` + a commented pystray tray loop. |
| `gem_sprites.rs` | Rust port; embeds the JSON with `include_str!`. |
| `v2-archive/` | The previous version, for rollback and diffing. |
| `HANDOFF-recrop-26.md`, `recrop_26.py` | Optional 26 × 26 recrop — **not applied**, see §7. |

**497 frames across 24 clips in 9 states.**

---

## 2. The states

`base` loops until the app says otherwise. `enter` plays once on arrival,
`exit` plays once before leaving. Everything else is fired by the kit's script,
or is a hold the app triggers by name.

| state | base | enter → exit | fires from the script | true when |
| --- | --- | --- | --- | --- |
| `idle` | `rest` | — | filler + 6 gags, see §3 | nothing asked of her |
| `working` | `typewriter` (70f) | `typewriter-in` → `typewriter-out` | — | executing on the machine |
| `thinking` | `orbit` (40f) | — | — | planning, no tool running |
| `listening` | `listen` (42f) | — | `misheard` *(hold)* | mic open, capturing |
| `speaking` | `speak` (42f) | — | — | TTS playing |
| `needs-permission` | `wait` (36f) | `lock-in` → `granted` | — | blocked until the user answers |
| `resting` | `sleep` (42f) | — | — | off duty / mic disabled |
| `done` | `settled` | `sparkle` *(hold)* | — | task finished cleanly |
| `error` | `held` | `fail` *(hold)* | — | task could not complete |

Clip policies:

- **`loop`** — repeats forever. Seamless; cut away on any frame.
- **`oneshot`** — plays once, then falls back to the state's base (or, if it was
  an exit, into the next state's enter).
- **`hold`** — plays once and **freezes on its last frame** until the app calls
  `release()` or changes state. `misheard`, `sparkle` and `fail` are holds
  because they are statements, not activity.

Default playback is **9 fps** (`fps` in the JSON, also per state). Do not run it
faster — the character is designed around a 110 ms beat.

---

## 3. The idle script — two tiers

`idle` is the state the user sees most, so it is the only one with real
behaviour. It has nine clips: a 1-frame `rest`, two everyday beats, and six
gags.

```jsonc
"script": {
  "restHold": [27, 45],                    // passes of rest between FILLERS
  "filler":   { "blink": 7, "look-around": 3 },
  "gagEvery": [7, 12],                     // fillers between GAGS
  "weights":  { "jump":1, "skip-rope":1, "guitar":1,
                "phone":1, "basketball":1, "disguise":1 }
}
```

A **filler** fires every `restHold` passes of `rest`. Every `gagEvery` fillers,
a **gag** fires instead. At 9 fps with a 1-frame `rest`:

| | rate |
| --- | --- |
| filler | every **4 s** — blink ~6 s, look-around ~13 s |
| gag | every **38 s** |
| any one specific gag | every ~3.8 min |
| gag duty cycle | **8%** of idle time |

**Why two tables and not one.** A single weight table cannot express "blink
often, play guitar rarely" — they draw from the same pool, so every blink is a
missed gag and the gag rate is hostage to the blink rate. Splitting them makes
the two rates independent.

All six gags sit at weight 1. With six of them, equal weighting already makes
each one rare; raise a single gag only to make it a signature. The gags run
1.0–4.6 s (`jump` 9f, `phone` 25f, `guitar` 30f, `skip-rope` 33f,
`basketball` 33f, `disguise` 41f).

Both players skip `hold` clips when rolling, so `listening/misheard` never fires
by itself — the app triggers it when parsing fails.

A state with **no `filler` key** falls back to drawing everything from
`weights`, which is exactly v2's behaviour. That is the compatibility hinge: a
v2 loader will run a v3 kit, but it will play gags where fillers belong, so Gem
performs constantly. **Use the loaders in this folder.**

---

## 4. Format

```jsonc
{
  "version": 3,
  "cell": 32,                        // every frame is 32 x 32 cells
  "fps": 9,
  "eyes": { "width": 2 },
  "anchor": { "x":6, "y":6, "w":20, "h":20 },   // tight box, for legacy crops
  "body":   { "x":9, "y":11, "w":14, "h":13, "eyeCols":[12,18], "eyeRows":[16,17] },
  "palette": { "1": { "role":"body", "light":"#1B1714", "dark":"#FBF9F5", "use":"…" }, … },
  "atlas":   { "file":"al-atlas.png", "cell":32, "columns":70, "rows":24, "order":[…] },
  "states": {
    "idle": {
      "fps": 9, "base": "rest", "enter": null, "exit": null,
      "order": ["rest","blink","look-around","jump","skip-rope","guitar","phone","basketball","disguise"],
      "script": { … see §3 … },
      "clips": { "rest": { "policy":"loop", "row":0, "frames":[ […32 strings] ] }, … }
    }
  }
}
```

Each frame is 32 strings of 32 characters. `.` is transparent; a digit is a
palette role:

| index | role | light | dark | used for |
| --- | --- | --- | --- | --- |
| `1` | body | `#1B1714` | `#FBF9F5` | the disc |
| `2` | eye | `#FBF9F5` | `#1B1714` | eyes and mouth, knocked out of the body |
| `3` | purple | `#6C4BE8` | `#8E72FF` | system surfaces and activity |
| `4` | orange | `#D97A28` | `#E8913F` | held props, emissions, errors |
| `5` | gray | `#9A94A6` | `#847E8C` | physical objects (phone, sleep Z) |
| `6` | shade | `#584E45` | `#B9B1A6` | local depth only: hands, pen shadow, guitar fingers |

**Ship the indices, not the colours.** Recolouring is a map lookup, so dark
tray, light tray, high contrast and disabled are all the same frames.

### Eyes

**2 px wide × 2 tall**, at columns 12–13 and 18–19. v2's were 1 px wide, which
put the eye at 1:14 of the body width; Clawd's square eye is 1:8 of its body, and
2 px brings Gem to 1:7. A 1 px eye is also the first thing destroyed by a
fractional downscale, and in the tray template the eyes are transparent *holes* —
a 1 px hole closes up visually long before a 2 px one does.

`resting/sleep` draws its closed eyes 2 px wide already and is unchanged.

### Atlas

Row = `states[state].clips[clip].row`, column = frame index. `atlas.order` lists
the rows top to bottom. Cells past a clip's frame count are empty, so **read the
frame count from the JSON**, never from the PNG width.

Note the atlas is **taller than v2's** (24 rows, not 21). Anything that
hardcoded the sheet height or a row count needs updating; the cell size and
column count are unchanged.

---

## 5. Deploying

### Tray / menubar icon

Use the template palette: body in one colour, eyes mapped to **transparent** so
they are holes in the silhouette. This is what macOS expects from a menubar icon
(it inverts the template itself) and it is what keeps Gem legible small.
Both loaders ship `tray()` / `palette_tray()` for this.

Render at an integer scale only — 32, 64, 96 px. Never a fractional scale;
nearest-neighbour at 1.5× destroys the cells.

**On a 2× display a 22-point menubar slot is 44 real pixels.** Supplying a 32 px
bitmap means the OS scales it *up* by 1.4×, which is blurry — the most likely
cause of Gem looking soft. Supply 64 px (scale 2) and declare the logical size.
Worth testing before changing any artwork.

### In-window / larger surfaces

Use the light or dark palette and any integer scale (`scale=4` → 128 px).
Nearest-neighbour resampling, never smooth.

### Rules that keep it coherent

- One state at a time. Props and badges never stack.
- Purple (`3`) is system surfaces — the typewriter, the permission lock, activity.
  Orange (`4`) is what Gem holds or emits. Do not swap their jobs.
- Shade (`6`) is local depth on the body only; it is not an outline colour.
- Holds (`sparkle`, `fail`, `misheard`) must be released by the app — either
  `release()` or the next `set_state`. Never leave one on screen indefinitely.
- Anything that lasts under ~400 ms does not deserve its own clip.

---

## 6. Wiring it up

### Python

```python
from gem_sprites import GemSprites, GemPlayer, TRAY_DARK

kit = GemSprites("assets/gem/al-sprites.json")
gem = GemPlayer(kit, "idle")

while True:
    icon.icon = gem.image(palette=TRAY_DARK)   # PIL.Image, RGBA
    time.sleep(gem.tick())                     # advances; returns seconds

gem.set_state("working")     # plays typewriter-in, then typewriter
gem.hold("misheard")         # freezes until the next set_state / release()
kit.frame("idle", "guitar", 4, scale=4)        # a single frame, no player
kit.gif("idle", "disguise", "docs/disguise.gif", scale=4)
```

Requires Pillow. `pystray` only for the tray example at the bottom of the file.

### Rust

Drop `gem_sprites.rs` and `al-sprites.json` in the same module directory; the
JSON is embedded at compile time. Needs `serde` + `serde_json`, no image decoder,
no `rand`.

```rust
let gem = GemSprites::load();
let pal = palette_tray([255, 255, 255, 255]);
let mut player = Player::new(&gem, "idle");

loop {
    tray.set_icon(player.rgba(1, &pal));       // Vec<u8>, RGBA8, 32*scale square
    sleep(Duration::from_millis(player.tick()));
}
```

`player.atlas_cell()` returns `(state, clip, row, column)` if you would rather
blit the atlas than build buffers.

The port changes the loader only. The artwork, the state and clip names, the
loop lengths, the script and the palette are identical across both languages.

---

## 7. Upgrading from v2 — checklist

1. Replace `al-sprites.json` and all three PNGs.
2. Replace `gem_sprites.py` / `gem_sprites.rs`. **Required** — the old loader
   ignores `filler`/`gagEvery` and will make Gem perform a gag every 4 seconds.
3. Check anything that hardcoded the **atlas height or row count** (21 → 24
   rows). Cell size and columns are unchanged.
4. Nothing else in the API moved: `GemSprites`, `GemPlayer`, `frame()`,
   `tick()`, `set_state()`, `hold()`, `release()`, the palettes and
   `atlas_cell()` all keep their signatures.

### Still open: the 26 × 26 recrop

`HANDOFF-recrop-26.md` and `recrop_26.py` are **not applied to this export**.
They shrink the cell from 32 to 26 by cropping margin that is empty in every
frame, which makes Gem render **1.23× larger** at the same icon size. It is a
mechanical transform with one frame to redraw. Left out of v3 deliberately —
settle the retina supply size in §5 first, because if that is the real cause of
"Gem looks tiny", the recrop is the smaller of the two fixes.

---

## 8. Suggested layout in the repo

```
assets/gem/
  al-sprites.json
  al-atlas.png
  al-atlas-4x.png
  al-tray-template.png
not-hal/ui/
  gem_sprites.py          # or src/gem_sprites.rs
```

Which surface shows which state is the app's call — this kit only guarantees
that every state exists, loops cleanly, and reads small.

---

## 9. Regenerating

The sprites are generated and edited in `gem-lab-v3/Gem Sprite Builder.dc.html`
and reviewed in `gem-lab-v3/Gem Contact Sheet.dc.html`; `gem-lab-v3/states.json`
is the raw output. Change the art there and re-export — both the design sheet and
the app move together. Never hand-edit `al-sprites.json` or repaint the PNGs;
the next export overwrites them.
