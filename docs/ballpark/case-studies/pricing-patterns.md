# How Tyler prices a Preliminary Ballpark: patterns from 20 real ballparks

Nov 17, 2026. Source: the latest Preliminary Ballpark document on each of the 20 most recent jobs that
have one (Jul–Nov 2026), pulled from JobTread. Every line with a cost is in `ballpark_line_items.csv`
(1,192 lines; each job's total matches its JobTread document cost). Seven of the 20 are
"Preliminary Ballpark (Fixed fee)" documents.

Jobs: 00892 Barber, 00895 Gentles, 00904 McDowell, 00882 Harteg (FF), 00859 Strawderman, 00837 Stephens
Porch, 00822 Greenway, 00691 Helburg Addition, 00806 Woods Addition (FF), 00749 Frandsen, 00650
Graf-Meintzer Porch (FF), 00708 Mettelka Kitchen (FF), 00571 Parker, 00763 Huntington (FF), 00676 Groenhof,
00687 Dittmer Mud Area (FF), 00636 Weissman Porch (FF), 00844 Malm, 00845 McNelly, 00835 Taggart.

## CompanyCam

Thin, as expected. McDowell and Harteg have no videos; Barber has 7 clips (16–83 s), Gentles 4 (38–132 s).
The two longest transcripts describe *what* is being done ("convert the office to a powder room", "tear off
the deck and rebuild it with a flat roof over a parking pad") but give almost no quantities. Tyler sets the
quantities from experience after the visit, so the budget lines are where the pricing logic lives. This
analysis therefore tests the tool's pricing rules, not its reading of a visit.

## Where Tyler's practice differs from the tool (strong patterns)

"n/m" = the pattern held on n of the m jobs that used the line.

| Line | Tyler's practice | Tool today | Evidence |
|---|---|---|---|
| Framing Labor | **$450/day**. 5–12 days small, 16–30 medium, 36–40 additions, 90 Frandsen | $300/day | 17/17 per-day jobs |
| Footing and Foundation | $42 × **LF of perimeter** (12–140) | $42 × foundation **SF** | Woods: 520 SF framed, 70 footing; Greenway 750 SF slab, 100 footing. A 500 SF addition costs ~$3,800 Tyler's way vs $21,000 the tool's |
| Framing Materials | $20 × framed area, 100–800 SF typical (Malm 2,200) | $20 × whole floor plan SF | 16/18 |
| Insulation | Same SF as framing materials, roughly | Whole floor plan SF | 12 jobs; only priced when walls open |
| General Electrical | **$9/SF of renovated floor area on almost every job**, not only rewires (100–4,600 SF) | Only with a full rewire | 18/20 |
| Electrical devices | Receptacle / switch / dimmer lines left at qty 1 (placeholders) on most jobs | Per-room rule: 1 per wall + 1 per 6 LF, 2 switches, 1 dimmer | Device counts above 1 on only 5 jobs |
| Recessed lights | ~1.5 per painted room (range 0.7–2.5) | 4 per room, 8 per kitchen | 13 jobs |
| General Plumbing | **$1,200 per fixture** (2–14), or a lump on the biggest jobs | Omitted (spec) | 14/20 |
| Finish plumbing hours | ~2.5–3 hrs per fixture (median) | Claude estimates | 11 jobs |
| Interior Paint | **$1,100/room** + one $450 Paint Materials per job (3–6 on 20+ room jobs) | $900/room incl. materials | 15/15 per-room jobs |
| Interior Trim Labor | ~**1 day per painted room** ($425/day); ~1.5 on the largest jobs | Claude estimates | median 1.1 day/room, 14 jobs |
| Drywall | Hang ≈ 300 SF per painted room; materials ≈ **1.35 × hang SF** | Same SF on both lines | median 1.33, 15 jobs |
| Primary Bath Tile | **$5,700** | $6,500 | 10/11 |
| Hall / other bath tile | **$2,200** | $3,500 | 6/6 |
| Backsplash Tile | **$1,800** | $1,650 | 5/5 |
| Ceramic Tile Installation | $1,200 × units, ~5 per primary bath + ~3 per other bath. **No tile material line on any job** | $1,200 per small area + a tile material allowance per SF | 14 jobs |
| Window installation | **$300** per window | $400 | 13/13 |
| Windows | $600 on most; $900–$1,800 on higher-end jobs | $450 / $1,100 / $2,100 by tier | 13 jobs |
| Gutters | **$1,200 per unit**: 1–2 for a porch/addition, 5–7 whole house | $2,500 placeholder | 12/13 |
| Hardwood Finishing | **$3.75/SF** | $3.25/SF | 11/11 |
| Countertops | $105–$125/SF on kitchens; $1,200–$3,200 lump for vanity tops | $92.50 / $142.50 / $237.50 by tier | 8 per-SF jobs |
| Bathroom Hardware | **$950** for a one-bath job; $1,600–$3,800 multi-bath | Per bath by tier ($300 / $850 / $2,200) | 8 jobs at $950 |
| Cabinet Hardware | $450 with no kitchen; $2,450–$3,750 with a kitchen | $450 per kitchen/bath | 16 jobs |

## Overhead lines

| Line | Tyler's practice | Tool today |
|---|---|---|
| Markup | **28% on 14 of 20 regular ballparks at every size** (Barber $656K cost on Nov 16, Parker $551K, McDowell $391K). 22–23% only on Malm and Frandsen. Fixed fee: 34–38% (Mettelka and Dittmer 28%) | 28 / 25 / 23% bracket by cost |
| Construction Management | $900/wk (18/20); weeks scale with size (8–16 small, 18–26 medium, 40–45 largest) | $900/wk × duration (matches) |
| Port a John | ≈ weeks ÷ 4 | weeks ÷ 4.33 (matches) |
| Dumpsters | ≈ 1 per 2–2.5 weeks on 13 jobs; **1 on 5 jobs** (incl. McNelly and Taggart, 24–26 wks) | 1 per 2 weeks |
| Dust control | ≈ 50–70% of the weeks (only while demo/rough is happening); 1 on McNelly and Taggart | Every week |
| Permits | $800 × 1–10, not a percentage: 1 for small interior jobs, 3–6 for most, 7–10 for big/whole-home. 0.6–3.1% of cost | 0.8% of cost (undershoots small jobs by $2–3K) |
| Warranty | ~1% of cost, min $1,200 (0.8–1.4%) | 1%, min $1,200 (**matches**) |
| On/Off Site Storage | None under ~$160K; 1 unit $230K–$660K; 2 on Malm, Frandsen, Parker | 1 always; 2 over $390,625 |
| Selection Coordination | $1,500–$3,500 under $250K; ~$5,000 (40 hrs) $300–400K; $6,250–$10,000 above $550K | 12 / 20 / 28 hrs by tier ($1,500–$3,500) |
| Punch list | 1 day under ~$160K, 2–3 up to $400K, 4–5 above | Tyler's form entry |
| Cleaning | $600 × 1; × 2 on $390K+ | 1 |

## Lines Tyler uses that the spec removes

- Preconstruction costs: 6 jobs, $1,500–$10,500
- Site Work / Grading: 10 jobs, typically $800 + $600, more on additions
- Engineering / engineering letter: $1,850–$2,800 on structural jobs
- Lot survey: $1,850 (Gentles)

## Lines that already match

CM rate, Port a John, warranty, glass shower door ($2,250), kitchen cabinetry ($625/LF), closets
(~$3,500–$4,800 per job; $350 small closets), appliance installation (16 hrs per kitchen), exterior paint
(lump ≈ $8/SF heated, or $1,200 units for partial), roofing ($1,100/square), cleaning rate, finish HVAC ($450).

## Questions this raises for Tyler / Jason

1. **Markup bracket.** Tyler priced Barber ($656K cost, Nov 16, after the Oct 27 bracket decision) at 28%.
   Is the 28 / 25 / 23% bracket the rule going forward, or is 28% the norm with exceptions for the biggest jobs?
   At 23% Barber would have priced ~$33K lower.
2. **What is a "Ceramic Tile Installation" unit?** The pattern is ~5 per primary bath and ~3 per other bath.
   Is it an allowance covering tile material too (no job has a tile material line)?
3. **Dumpster and dust = 1** on McNelly and Taggart (24–26 week jobs). Placeholders, or real?
4. **Keep Preconstruction, Site Work, Grading, Engineering** as lines the tool can add when the scope calls for them?
5. **General Electrical on non-rewire jobs:** confirm $9/SF of renovated area is the normal electrical
   line, with device lines left at placeholder counts.

## What happened next

Nov 17, 2026: Jason chose to adopt these patterns as the tool's rules and tweak later. Done in
`ballpark_pricing.py` (markup bracket left as is); see "Tyler's practice adopted as the rules" in
`../build-status.md` for the full list and the McNelly / Taggart / Malm checks.
