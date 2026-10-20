# Room File Format (`occ-scope-rooms/1`)

The structured file the Site Scope Tool works from. Phase 4's quick pass writes it from the dictated notes and photos (plus the question-round answers). `scope_drawings.py` draws it (phase 2). Phases 5-6 read it for the scope Page and the budget. Drawing rules: `floor-plan-drawing-rules.md`. Worked example: `samples/test1-office-2026-10-19.json` (the Oct 19 Test (1) office).

To draw a file and look at the PNGs: `python3 scope_drawings.py <file.json> <output folder>`.

## Conventions
- **All sizes are inches.** Drawings print feet and inches (`13'-2"`).
- **Every measurement carries its source:** `{"in": 36, "src": "est"}`.

  | `src` | Means | Printed as |
  |---|---|---|
  | `dictated` (or `confirmed`) | Said in a note, or answered in the question round | `3'-0"` |
  | `est` | Estimated from a photo | `3'-0" (est.)` |
  | `assumed` | Default, nothing to go on | `3'-0" (assumed)` |

  A bare number (`36`) has no source, so it is treated as `assumed`. Derived numbers take the weaker source (a head from an estimated sill + a dictated height is `est`).
- **Walls are walked clockwise from Wall 1** (the entry/door wall). Each wall turns right 90 degrees into the next unless it says otherwise.
- **Left corner:** standing in the room facing a wall, its left corner. Every `offset` is measured from there, to the edge of an opening (not the casing) or the edge of an item. Devices (outlets, switches, lights, conduit) are a single point: `offset` is to their center.
- **Heights are from finished floor** (AFF).
- **`status`** on walls, openings, items and finishes: `existing` (gray, the default), `remove` (dashed red), `new` (blue). Openings can also be `replace`: existing comes out, new one of the same size goes in (drawn both ways).

## Top level
| Field | Required | Notes |
|---|---|---|
| `schema` | yes | `"occ-scope-rooms/1"` |
| `project` | no | `name`, `address`, `visit_date` (`YYYY-MM-DD`) are printed under the drawing titles; `companycam_project_id`, `jobtread_job_id` and `job_number` are carried for the later phases. The quick pass fills this block in by code, not by the AI. |
| `rooms` | yes | Rooms (below). Each room gets its own plan. The quick pass may write `[]` when nothing was dictated wall by wall (an exterior-only or single-repair visit) - then there is nothing to draw and the work lives in `scope_items`. |
| `flags` | no | Open questions for the whole visit, as plain sentences. Printed in red under "CHECK BEFORE PRICING" on the first plan. |

### Added by the quick pass (phase 4)
The drawing code ignores these; the scope Page (phase 5) and budget (phase 6) read them.

| Field | Notes |
|---|---|
| `client_goals` | Short sentences on what the client wants, if said |
| `scope_items` | Every line of work: `{"id": "S1", "trade", "area", "work" (verb first), "quantity": {"value", "unit" (sf, lf, ea, sq, hr, ls), "src"} or null, "sub" (the sub Jason addressed, or null), "licensed_trade" (electrical, plumbing, HVAC, gas, structural), "photos": ["P4"], "notes": ["N3"], "detail"}` |
| `photos` | One per photo read: `{"ref": "P1", "id": "<CompanyCam photo id>", "area", "wall" (number or null), "description"}` - the photo descriptions phase 5 writes back to CompanyCam (appended, never overwriting) |
| `conflicts` | `{"what", "source_a", "source_b", "action"}` - the scope Page's measurement conflicts table |

`P1`, `N1`, `V1` are the photo, note and video labels the quick pass used (photos and notes in time order); they're only meaningful inside one run's file.

## Room
| Field | Required | Notes |
|---|---|---|
| `id` | no | Short slug used in file names (`office` -> `office-floor-plan.png`). Defaults from `name`. |
| `name` | yes | "Office", "Primary Bath", "Back Deck" |
| `ceiling` | no | Measurement, plus optional `note` ("exposed structure"). Default 8'-0" (assumed). |
| `walls` | yes | At least 3, clockwise from Wall 1 (below) |
| `finishes` | no | Finishes that run on every wall (baseboard, crown) - same shape as wall finishes |
| `notes` | no | Sentences printed under the plan |
| `flags` | no | Open questions for this room |

## Wall
| Field | Required | Notes |
|---|---|---|
| `number` | no | Defaults to its position (1, 2, 3...) |
| `length` | yes | Finished surface to finished surface |
| `turn` | no | Degrees turned at the end of this wall into the next. `90` (default) = normal outside corner; `-90` = inside corner (L-shaped rooms); other angles allowed. |
| `construction` | no | `interior` (default, 4-1/2"), `plumbing` / `2x6` (6-1/2"), `exterior` (6"), `exterior_2x6` (8"), `brick` (10"), `cmu` (8", always marked assumed), `half` / `knee` (4-1/2") |
| `construction_src` | no | Source of the construction call (`dictated`, `est`) |
| `thickness` | no | Measurement - overrides the table |
| `exterior` | no | `true` for an exterior wall whose construction isn't `exterior*` (e.g. exterior CMU) |
| `status` | no | `existing` / `remove` / `new` for the wall itself |
| `label` | no | Replaces the construction label on the plan |
| `openings` | no | Below |
| `items` | no | Below |
| `finishes` | no | Below |
| `notes` | no | Sentences printed under this wall's elevation |
| `elevation` | no | `true` / `false` to force. By default an elevation is drawn only when the wall has openings, items or its own finishes (rules section 10). |

## Opening
| Field | Required | Notes |
|---|---|---|
| `type` | yes | `door`, `window`, `cased_opening`, `pocket_door`, `slider`, `bifold`, `french_door` |
| `status` | no | `existing` / `remove` / `new` / `replace` |
| `offset`, `width` | yes | From the left corner |
| `height` / `head` | doors | Head height. Default 6'-8" (assumed). |
| `sill`, `head`, `height` | windows | Any two of the three |
| `hinge` | doors | `left` / `right` jamb, facing the wall from inside. Default `left`. |
| `swing` | doors | `in` (into this room, default) / `out` |
| `sashes` | windows | Number of side-by-side sashes (dashed dividers) |
| `casing` | no | `{"width": <measurement>, "profile": "3-1/2\" colonial", "status": "new"}` - drawn on the elevation; the profile is a label, not a drawn shape |
| `label` | no | Elevation label. Default like `NEW door`, `exist. window`. Use `\n` for a second line. |
| `plan_label` | no | Plan label if different. `""` means no label. |

## Item (things on or against a wall)
| Field | Required | Notes |
|---|---|---|
| `type` | yes | Boxes: `base_cabinet`, `upper_cabinet`, `vanity`, `appliance`, `tub`, `shower`, `toilet`, `chase`, `whiteboard`, `mirror`, `niche`, `shelf`, `other` (any word works). Points: `outlet`, `switch`, `light`, `valve`, `device`, `conduit`. |
| `status` | no | |
| `offset` | yes | To the left edge (boxes) or center (points) |
| `width` | boxes | Without a width an item is drawn as a point |
| `bottom`, `top`, `height` | boxes | Any two. `top` can be `"ceiling"`. Defaults: base cabinet 0 to 34-1/2", vanity 0 to 34", upper cabinet 54" to 84" (assumed). |
| `height` | points | Center height (outlets default 1'-4" assumed) |
| `bottom`, `top` | conduit | Vertical run |
| `depth` | no | Plan depth. Defaults: base cabinet 24", upper 12" (dashed), vanity 21", tub 30", appliance 24". |
| `doors` | cabinets | Number of door divisions |
| `drawers` | cabinets | `false` for no drawer row (default `true` on base cabinets) |
| `countertop` | base cabinets | `false` for none (default `true`, 1-1/2") |
| `mount` | devices | e.g. `"surface"` - kept for the scope write-up |
| `plan` | no | `true` / `false` to force it onto the plan. Default: cabinets and fixtures yes; devices only when new or removed. |
| `above` | no | `true` draws it dashed on the plan (above the cut line) |
| `dimension` | no | `true` puts an existing item on the elevation's dimension string (new and removed items always are) |
| `label`, `plan_label`, `short` | no | `short` is the word used on the height labels ("whiteboard bottom") |

## Finish (on a wall, or on the room for all walls)
| Field | Notes |
|---|---|
| `type` | `baseboard`, `crown`, `chair_rail`, `wainscot`, `tile`, `backsplash`, `accent` / `paint` |
| `status` | |
| `height` | Baseboard/crown: the profile height (default 3-1/2" assumed). Chair rail: its height (default 36" assumed). |
| `bottom`, `top` | Wainscot/tile/backsplash extent. `top` can be `"ceiling"`. Defaults: wainscot to 36", backsplash 36" to 54". |
| `offset`, `width` | Part of a wall only (default the whole wall) |
| `spacing` | Wainscot panel / board-and-batten spacing |
| `tile_size` | Draws course lines |
| `profile`, `label` | Labels |

Finishes stop at openings and at cabinets/fixtures in front of them (no baseboard behind a base cabinet).

## Checks the code does
- **Closure:** the walls must meet back at Wall 1's start within 1". If not, the plan shows the gap in red ("DOES NOT CLOSE - off 0'-4"") and it goes on the flag list - nothing is quietly adjusted.
- Openings or items that run past the end of their wall; openings that overlap (a removed opening and its replacement don't count; a transom above a door doesn't overlap it); a window head above the ceiling.
- A file that can't be drawn (fewer than 3 walls, a wall with no length, an opening with no offset or width, a non-number) raises `DrawingError` with a sentence saying which wall.
