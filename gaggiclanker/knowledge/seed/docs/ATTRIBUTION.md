# Where these documents came from

Every markdown file in this directory (and its subdirectories) is a copy of a
knowledge file from [gaggimate-mcp](https://github.com/julianleopold/gaggimate-mcp),
which is MIT-licensed, verbatim except for gaggiclanker's own notes, which are marked
*gaggiclanker note*, three files that have passages cut or moved out (see "What was
trimmed" below), and the four drink documents in `drinks/`, which are gaggiclanker's own
text (see "The drink documents" below). They are shipped here so gaggiclanker's knowledge tier 2
can be seeded from files in the repository the way the prompts and the rule tier
are — the copy in the database is the live one, the file is the default, and
`POST /api/knowledge/docs/{slug}/reset` puts the file's text back.

    MIT License
    Copyright (c) 2026 julianleopold

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## The sources behind the sources

gaggimate-mcp's own files name where each piece of knowledge came from, and each
file keeps its "Sources" section here — that section is part of the chunked text,
so an excerpt quoted into an analysis carries its provenance with it. The
recurring names:

* **gaggimate-barista** (Charlie Hall, MIT) — most of the dial-in heuristics:
  the variable hierarchy, the roast → temperature table, the pressure matrix,
  the taste → cause mappings. gaggimate-mcp adapts them; this is an adaptation
  of that adaptation.
* **Lance Hedrick** — the extraction timeline, the 5 g adjustment rule, the
  sour-versus-bitter discrimination method (`ESPRESSO_BREWING_BASICS.md`,
  `ESPRESSO_TASTING_GUIDE.md`).
* **Scott Rao** — sour *and* bitter in the same cup is channeling, not an
  extraction level.
* **INEI, espressoaf.com, Decent DE1, gaggiuino** — the standards and the
  measured thresholds in `research/ESPRESSO_PHYSICS_AND_THRESHOLD_CALIBRATION.md`.
* **docs.gaggimate.eu** and the **Espresso Aficionados Discord** — machine
  behaviour, the Automatic Pro profiles (modsmthng).

## What was trimmed, and why

`diagnostics/SHOT_DIAGNOSTICS_REFERENCE.md` and
`research/ESPRESSO_PHYSICS_AND_THRESHOLD_CALIBRATION.md` are the upstream text
with passages **cut, never reworded**, because gaggiclanker no longer produces
the labels they describe. Cut from the first: the band tables and the label
lists with their thresholds, the annotations that were band labels, the whole
channeling block (its indicators, descriptors, risk computation and
annotations), and the per-phase annotation labels, with the cheat-sheet and
strategy lines that were written in those labels. Cut from the second: the
resistance bands, the calibration decisions and the label naming decision for
the bands, and the section that tabulated every band's calibration and the
channeling risk scoring. "Channeling" as a physical effect in the puck, in any
document, is untouched: it is not a label.

`MILK_AND_DRINKS.md` has its "Espresso Drinks" section moved out, and one sentence of it
cut; see the next section.

## The drink documents

`drinks/ESPRESSO.md`, `drinks/AMERICANO.md`, `drinks/CAPPUCCINO.md` and `drinks/LATTE.md` are
gaggiclanker's own text, written for the model and for a person reading the knowledge base:
what each drink covers, what the shot has to do in that cup, what that tends to mean for the
recipe, what tastes wrong and what it usually points to, and how the drink notes matter. Their
proportions and tendencies come from gaggimate-barista (Charlie Hall, MIT) as gaggimate-mcp
adapts it, and from common barista practice.

What moved out of `MILK_AND_DRINKS.md`, from its "Espresso Drinks" section, with the table rows
and sentence unchanged:

* into `drinks/CAPPUCCINO.md`: the "Drink Specifications" rows for the macchiato, cortado,
  piccolo, cappuccino (traditional and modern) and flat white; the "Drink Format Recommendations"
  rows for a bright, fruity, delicate shot, a sweet, balanced, medium-bodied one and a
  clarity-focused one; and the sentence "Extract for the bean's best expression, then match the
  right drink format."
* into `drinks/LATTE.md`: the "Drink Specifications" row for the latte and the "Drink Format
  Recommendations" row for an intense, heavy-bodied, bold shot.

Cut, and not moved: the sentence "Never adjust grind, ratio, pressure, or temperature to 'make
the shot work in milk.'" The drink documents say the opposite, that what a shot has to do
depends on the cup it ends up in. `MILK_AND_DRINKS.md` keeps the steaming sections, the milk
types, the single-boiler workflow and the troubleshooting table, and its opening line now points
to the Cappuccino and Latte entries. `ESPRESSO_BREWING_BASICS.md` is untouched: it keeps its
ratio table and shot styles, which `drinks/ESPRESSO.md` names instead of copying.

## What was left out, and why

* `sources/yt-videos/` — a transcript summary of somebody else's video. The
  licence of the underlying work is not ours to assume, so it is not copied.
  The heuristics it contributed are already in `ESPRESSO_BREWING_BASICS.md` and
  `ESPRESSO_TASTING_GUIDE.md`, which cite it.
* `automatic-pro/profile_files/*.json` — profiles, not prose. They belong to the
  profile library rather than to a text index, and chunking JSON produces
  fragments no retrieval query can usefully match.

`automatic-pro/How the Automatic Pro Profile works.md` is here as
`automatic-pro/AUTOMATIC_PRO_HOW_IT_WORKS.md`: the text is byte-identical, and
only the file name changed, because a document slug is part of a citation and a
citation with spaces in it is a citation nobody can paste.
