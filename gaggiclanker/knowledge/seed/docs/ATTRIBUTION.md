# Where these documents came from

Every markdown file in this directory (and its subdirectories) is a **verbatim
copy** of a knowledge file from [gaggimate-mcp](https://github.com/julianleopold/gaggimate-mcp),
which is MIT-licensed. They are shipped here so gaggiclanker's knowledge tier 2
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
