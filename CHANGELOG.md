# Changelog

Notable changes per release. Dates are the day the release was cut.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the versions are [semantic](https://semver.org/). Until 1.0 the database schema
may change between releases; migrations are forward-only and run at boot, so an
upgrade is `docker compose pull && docker compose up -d` — but take a backup
first (`POST /api/backup`), because there is no down-migration.

## [Unreleased]

### Flush from the top bar

- **A Flush button sits beside the Writes switch while writes are on.** One click runs the
  machine's own flush for the duration set on the machine, exactly as the Flush button on the
  machine's web UI does. It is refused, with a toast saying why, outside brew mode or while a
  shot is running, and is disabled while the machine is not connected. There is no
  hold-to-flush, and a flush is not recorded (not in Recent writes, not as a shot).
- `POST /api/device/flush` is the route behind it.

### Shots page: hide discarded shots

- **Hide discarded**, a tickbox on the Shots page on its own line under the buttons, right-aligned
  and ticked by default, leaves shots labelled Discard out of the list, its paging and its count.
  Untick it to see them; this browser remembers the choice for every later visit, and Clear all
  leaves it alone.
  A shot you label Discard while it is ticked leaves the list at the next refresh. The API's
  `GET /api/shots` takes `include_discarded=false` for the same thing and still lists every shot by
  default.
- Sync with machine stands apart from the list's own controls, with a wider gap before Filters.

### Beans export and import as JSON

- **Every coffee on the Beans page has an Export JSON icon**, which downloads it as one file
  (`ethiopia-guji.json`): its id and every field the form edits.
- **Import JSON** on the Beans page reads one such file. When the file's `id` is a coffee you have,
  the fields the file carries update it and the rest stay as recorded (`null` clears a field); with
  no id, or an id this archive does not hold, the file adds a new coffee. A toast says which. A file
  with an unknown field or a value outside the vocabularies is refused and nothing is written.
  The same thing is `POST /api/beans/import`.

### The judgement form: doses above the notes, prefilled, and no grind

- **Breaking: the grind a judgement recorded is deleted.** Migration 0050 drops
  `shot_judgements.grind_setting` and remakes `v_judgements` without it, and the `grind_setting`
  field leaves the judgement API; whatever people typed into the form's Grind box is gone. The grind
  a shot was brewed at is the Set version's recipe grind (`recipe_grind`) or the machine's own note
  (`note_grind`), both unchanged. The shot-information item `grind_as_brewed`, its method id and
  its glossary entry go with it (a tier you chose for it is dropped), and importing or syncing the
  machine's notes card no longer copies its grind into a judgement.
- **Dose in and dose out sit above the notes, and start at what the shot says.** Dose out starts at
  the scale's yield when the shot has a scale, dose in at the Set version's dose when the shot is
  filed in a Set; without them the fields are empty. They are values, not placeholders, you can edit
  them, and nothing is saved until you save the judgement; a value you saved earlier wins. The ratio
  is no longer in the form: it stays on "The shot" card and in what the chat is told, where your own
  dose still overrides the scale's.
- **The grind is set by the Set version.** "New version from this shot" has its own **Grind** field,
  starting at the Set's current recipe grind and becoming the new version's grind (with its number);
  the doses still come from your judgement, and emptying the field inherits the parent's grind.

### A shot's review is claims you can reject, kept apart from the checks the numbers make

- **Breaking: every stored review is deleted, and so is any edit you made to the two review
  prompts.** Migration 0048 replaces the review with the one below, and the old reviews hold a
  blind taste prediction (retired: taste stays yours), a paragraph and a summary, no claims, and
  nothing a person wrote, so there is nothing to carry. Their ids are never handed out again.
  **If you edited the `review` or `review-user` prompt on the Prompts page, that edit is
  deleted** (the output they ask for no longer fits what a review is checked against) and the
  shipped prompts are put back at boot; the tiers you chose for the seven retired review items
  go too. Nothing else is touched and no database needs deleting.
- **A review is claims about windows of the shot, with numbers the server worked out.** Started
  by a click on one shot, it writes a one-sentence summary and 1 to 12 claims, each tied to a
  phase, a span between two moments or the whole shot (so the curve can highlight it), with a
  fault word from the fixed list or none, one sentence with no figures in it, and one to three
  metric-language expressions. The server evaluates every expression on the shot and stores the
  value, the unit, the kind of channel (measured, estimated, commanded), why it is absent when it
  is, the limit the way a check words it ("at most 15 % of target", a share always a
  percentage), and whether the expression's own comparison held: no number beside a claim was
  typed by the model, and the summary carries none either. A claim whose comparison fails, or
  whose evidence could not be measured at all, is kept and marked as not borne out by the
  numbers. It also answers each free-text expectation of the confirmed signature (held or not,
  where, in a sentence), and, when the Set version was filed with a prediction, says how the shot
  moved against it (as predicted, partly, against, not shown). It predicts no taste, advises
  nothing and proposes nothing: the output has no field for any of them. Its input is the shot
  with its checks first, the Set version's recipe and prediction, the signature's free-text
  expectations, the profile, the style, and the knowledge rules and excerpts as before, and never
  your judgement, the machine's note, your label, another shot or an earlier review. Discarded
  shots cannot be reviewed (409), nor quarantined ones (422). A review's one running row per
  shot is enforced by the database as well as by the task registry.
- **Breaking: the one-line reason you could type when rejecting a claim is removed.** Rejecting is
  one click now, so migration 0049 drops the `reason` column of `review_claims` and the `reason`
  field of the answer; reasons already typed on claims are deleted with it. Nothing else of an
  answer is lost.
- **Every claim is kept unless you reject it, and that is what the chat hears.** A claim is
  written `confirmed`; `PATCH /api/reviews/{id}/claims/{claim_id}` with `{status: "confirmed" |
  "rejected"}` rejects one or restores it (an answer can be changed) and returns the updated
  review. There is no confirm-all and no waiting state, and the prediction stance is shown like
  any claim. Only the newest finished review of a shot can be answered (409 `REVIEW_SUPERSEDED`
  otherwise). That review is the one in force, for everyone: reviewing again changes only the
  badge's words (`Reviewing…`, `Failed to run`) and the sort bucket while it runs or if it fails,
  and sets the old one aside, rejected claims included, only when the new one finishes. The event
  `review.answered` follows an answer in another tab. Migration 0049 turns the claims of an
  earlier build that were still waiting into kept ones, and keeps each answer.
  The chat's shot information has a **Review** group (all base): whether the shot was reviewed
  and how many claims were kept or rejected, the kept claims with their numbers (a free-text
  expectation's answer says which expectation and whether it held), and the stance on the
  prediction. **The summary and a rejected claim are never served.** The SQL tool gains
  `v_review_claims` (the claims of a shot's newest finished review that were not rejected) and
  `v_reviews` counts kept and rejected claims. A prompt you edited keeps your text on boot: reset
  `chat-set`, `chat-general` and `chat-design` on the Prompts page to read the new paragraph on
  reviews. The tiers you chose for the three items follow their new names.
- **The Curve check and the review are two blocks.** Every shot served in the list, the detail
  and the fields route carries `checks` (`badge`, `entries`) and `review` (`state`, `badge`,
  `entries`, `verdict`, `summary`, `reason`, `review_id` and `in_force_id`, the id claims are
  answered through: the newest finished review, which `review_id`, the newest attempt, differs
  from while one runs). **The Curve check is only the deterministic checks and warnings**
  (`ramp: early yield +1`): the confirmed signature's failed critical and important expectations
  and the universal warnings, worked out whenever the shot is read, on every shot and with no
  click. A review never changes it, and a free-text expectation is not part of it: it reads
  "checked by the review". **The review is only what the model wrote**: its faults in the same
  `phase: fault +N` form, built by code from claims you did not reject (a failed free-text
  expectation, red for critical and amber for important, and a claim that carries a fault word,
  amber), `As intended` (a confirmed signature and no fault), `No faults`, `Reviewing…` or
  `Failed to run`. A claim the numbers do not bear out is **not a fault**: it is left out of the
  badge, its "+N" and the sort, stays in the Review box marked as before, and still reaches the
  chat unless you reject it. The shots list sorts by either: `?sort=check` (failures by severity and where
  in the shot, then the rest, newest first) and `?sort=review` (faults first, then `Failed to run`,
  `Reviewing…`, `No faults`, shots not reviewed yet, `As intended`, and last shots nobody can
  review), each a total order paged by offset. In the chat the checks are exactly the Curve
  check's; the model's answer to a free-text expectation is a line of the Review group.
- **On screen: two columns, and boxes you can fold.** The shots table's old Review column is now
  **Curve check** (the badge only, on every shot, discarded and quarantined ones included, never a
  button) and **Review**: a **Review** button for a shot nobody has reviewed, `Reviewing…` while
  one runs (inert), a grey `Failed to run` with a **Retry** button, or the model's faults; a
  discarded or quarantined shot has nothing in it. The buttons never open the row, a double click
  makes one request, and pressing a reviewed badge opens the row with its Review box expanded. The
  table follows a review by itself and polls every few seconds while any row is `Reviewing…`. Curve
  check is added in front of Review in a layout you stored that shows Review, once; any other
  choice is kept. The Set history and the compare tray show the Curve check only. The shot page
  and the open row draw the **same boxes in the same order**: Your judgement (open), the
  version's prediction (it does not fold), Curves, Review and Curve check (folded), and each box's
  state is remembered in your browser. The Curve check box is always there: with nothing to list
  it says one plain line ("Nothing to check: no confirmed signature and no warnings", or "No
  check failed" when a confirmed signature held). Folding Curves draws no chart and asks for no curve. The **Review
  box** opens on the verdict, built by code in the badge's words and colour, then the model's
  summary, labelled as its words, the free-text expectations it answered, the stance on the
  prediction, and the claims each with its numbers and **Reject**; "N rejected · show" brings the
  rejected ones back with **Restore**; **Review again** asks once, in place, that it replaces the
  current review. A claim whose numbers do not bear it out says so. The Review badge's hover lists its faults and then "The model's summary: …" (under `Failed
  to run`, "Last review: …", the earlier review still in force). Hovering or focusing a claim
  marks its span on the curve behind the lines, and pressing it pins the mark and opens Curves
  if it was folded; a Curve check line for a free-text expectation opens Review at its answer.
  The open row's boxes are as wide as the table's visible box and stay in view while the columns
  scroll sideways, so nothing in them is cut.
- **A fix carried over from the signatures work.** The review's style detection and rule tokens no
  longer read puck flow or pressure from a shot flagged without a pressure sensor.

### A profile can say what it is for, and every shot is checked against it

- **A signature per profile version.** What a profile is for is written as expectations, each
  with a tier (critical, important or context), a phase the profile names (or none, for the whole
  shot) and a kind: a **measure** (a number from the metric language held against a limit, such
  as the cup at the end of the ramp as a share of the target yield, at most 0.15), a **phase
  that must begin**, a **warning that is part of the design** (fast flow on a turbo's main
  phase), or **free text** that only the per-shot review will check. Values are relative to the
  target yield or the dose where they can be, so one signature carries across beans and doses. A
  failed expectation is named `phase: fault` with a word from the fixed list, worked out from the
  measure's channel and the side it failed (the cup over its limit is `early yield`, under it
  `little yield`; scale or puck flow over is `fast flow`, under `slow flow`; pressure `high
  pressure` or `low pressure`; `temperature`; `unstable`; `cut short`; a whole-shot yield `over
  target` or `under target`); a measure whose failing side has no word is refused when proposed.
- **An agent proposes, a person confirms, once per profile version.** The Set chat and the
  design chat get `propose_signature`, the Set chat also `propose_signature_override` (a
  different limit for one confirmed measure on this version, for example "at most 0.20" on a
  coarser bean), and `draft_profile` can carry expectations with the draft. All of them write
  **proposed** rows only. Nothing proposed or rejected is ever a check, in a badge, in the shots
  list's order or in what any agent reads: only a conversation's own proposals are shown to it,
  marked proposed, not confirmed, and a rejection reaches the conversation that proposed it with
  the reason the person gave. `propose_signature` refuses an expectation already proposed or
  confirmed on the profile version, and a profile whose different phase names are the same in
  their first 24 bytes. A new profile version is proposed the previous version's confirmed
  expectations (one click to confirm each, or all at once); one whose phase no longer exists is
  marked as needing a new phase and cannot be confirmed, and nothing is matched by position or
  by guess. New routes: `GET /api/profile-versions/{id}/signature`, `POST
  /api/signature-expectations/{id}/confirm`, `/reject` (with an optional reason) and `/tier`,
  `POST /api/profile-versions/{id}/signature/confirm-all`, and, for a Set version's override,
  `GET /api/sets/{id}/versions/{id}/signature-overrides` with `POST
  /api/sets/{id}/signature-overrides/{id}/confirm`, `/reject` and `/withdraw` (a person can take
  back a confirmed override, after which a new one can be proposed). An override's limits are
  also served as a person reads them (`limit_text`, `profile_limit_text`: "at most 20 % of
  target").
- **Confirm a signature on the Profiles page.** Every profile version has a **Signature** card,
  open when something waits for an answer. It lists the expectations in tier order with their
  phase, sentence, fault word, kind (computed, phase reached, warning expected, checked by the
  review) and status. A proposed one can be **confirmed**, **rejected** (with an optional
  reason, which the proposing conversation is told) or moved to another tier, and **Confirm all**
  answers every waiting one with one call. One carried from an earlier version says which, one
  whose phase the version no longer has says **needs a new phase** and cannot be confirmed, and
  who proposed one links to the conversation or the draft. A version nobody proposed anything
  for says so and links to the chats of the Sets that brew it. An expression cannot be edited
  by hand: ask the agent to propose it again.
- **The shot page shows Checks, and the Review badge is coloured by them.** The Warnings card is
  now **Curve check**, a box below the review: red, amber and grey lines as the
  server orders them, each with its value against its limit ("117.2 % of target, at most 15 % of
  target"), with the held ones and the ones only a review can check folded away under their
  count. A shot read without a confirmed signature says so and links to the version's Signature
  card. The Curve check badge takes its colour from its first entry: red, amber, or grey for a
  warning the signature expects (a turbo's fast flow). The shot page and the shots list's open
  row show them all.
- **The Set page shows its profile's signature.** The Now brewing card says whether the current
  version's profile has a signature (confirmed with N expectations, proposed, or none) and links
  to it. A Set version's overrides show on that version ("ramp: at most 20 % of target here
  (profile: at most 15 % of target)"), where a proposed one is confirmed or rejected and a
  confirmed one withdrawn.
- **The Warnings group is now Checks.** A shot's checks are one ordered list: failed critical
  expectations (red), failed important ones (amber), the universal warnings nothing marks as
  expected (amber), expected warnings (grey), what could not be measured (with its reason:
  neither held nor failed), then what held, the context expectations and the free text. A failed
  expectation supersedes the universal warning that says the same thing about the same phase. The
  catalogue's base item carries the signature's state (`signature: confirmed, 6 expectations` or
  `signature: read without a signature`), what failed and what could not be measured; the new
  extended item carries the rest. `GET /api/shots/{id}/fields` serves the ordered list
  (`checks`, with each check's tier, phase, fault word, sentence, value and unit, the effective
  limit after any override as `compare`, `relative_to` and `limit_text` ("at most 15 % of
  target"), whether it held and why it is absent; a share is served as a percentage, and a phase
  that must begin has no number) and the signature's state, and its `warnings` and `badge`, like the shots
  list's, now come from the same list, so the Review column reads `ramp: early yield +3` in red
  for the constructed lever shot once its signature is confirmed, and as before (amber) without
  one. A shot's checks are worked out whenever it is read and nothing about them is stored, so
  confirming an expectation or an override changes every shot at once with no re-derivation. They
  are worked out from the samples and phases already stored (a log is never parsed on a read) and
  remembered per process until something they depend on changes, so the shots list sorted by
  Review stays fast with hundreds of signed shots. A
  tier you set on the Warnings item follows it to Checks.
- **The Set chat is told what its profile is for.** The confirmed signature is in the profile block
  at the very top of the opening context, one line per expectation with the tier first, and the
  Set version's own confirmed limit beside the profile's; a profile version with none says so and
  asks the chat to propose one when the conversation turns to how its shots behave. Each shot
  in the opening context costs about 10 tokens more (the signature's state line), the base
  glossary about 130 more and the new rules paragraph about 130 more per request, and the extended
  meanings about 80 more once per answer that reads one; a six-expectation signature is 150 to
  300 tokens in the profile block. `get_profile` serves the
  confirmed signature. A prompt you edited keeps your text on boot and so keeps the old wording
  about warnings: reset `chat-set`, `chat-general`, `chat-design` and `review` on the Prompts page
  to read "the warnings among the Checks lines" and the new paragraph about checks against a
  signature, or add them yourself. The signature reaches an edited prompt either way, since it
  travels in the opening context.
- **Upgrade.** Migration 0046 adds three tables and 0047 moves a tier you chose for the Warnings item
  to Checks; nothing is lost and no database needs deleting.

### The Set chat starts with its profile in full and each version's averages

- **The profile is the first thing in the opening context.** A Set conversation is now handed
  the profile its version brews, as stored (the profile version's own document as compact JSON:
  each phase's pump target, transition, duration and stop conditions, with nothing the machine
  adds), under a heading that names the version, before everything else. Nothing that changes
  between turns (the shot and version counts, a grade, a waiting proposal, a revert, the ledger,
  the shots) comes before it, so the start of the prompt stays the same for the provider's cache.
  When the version it is compared against brews a different profile version, that profile
  follows under its own heading; on the same profile version it is written once. A version that
  names no profile says so. It costs about 300 to 550 tokens once per request, more with a
  compared profile.
- **`get_profile` serves the stored document too.** It used to hand back the document re-rendered
  in the machine's shape (with `favorite` and `selected`, a `temperature: 0` on every phase,
  `3.0` for `3`); it now returns exactly what the archive stores for that version, which is also
  what `draft_profile` merges its patch into. Its description says the opening context already
  shows the profile a version brews, so the tool is for other profiles and versions. The design
  chat is otherwise unchanged. A prompt you edited keeps your text on boot and so keeps the old
  wording: reset it on the Prompts page (Settings → Prompts, `chat-set`) to have the Set chat
  told that its profile is already given, or add the sentence yourself ("`get_profile` is for
  other profiles and versions"). The profile reaches an edited prompt either way, since it
  travels in the opening context.
- **Each ledger line carries the version's averages.** Mean shot time, yield, ratio, rating and
  first drip over the version's counted shots (not quarantined, not incomplete, not a Discard),
  from the one function `get_set`'s trajectory reads, which now also serves the mean yield and
  first drip. A version with shots but none that count says "no counted shots", and a mean over
  fewer shots than the version has says how many it is over. These per-version averages used to
  cover every shot; they now cover counted shots only, so a discarded or incomplete shot no
  longer pulls `get_set`'s trajectory, and the design chat's mean rating for a Set's versions
  (which reads the same numbers) now averages counted shots only too. The Set page's chart
  draws per-shot points and is unchanged.

### A phase name longer than 24 bytes is matched in the shot's log

- **Fixed: a long phase name never matched.** The firmware logs the first 24 bytes of each phase's
  name, the profile holds all of it, and the metric language compared the two as strings, so a
  window over a phase called "Pre-infusion with a long soak" (or a span anchored on it) read "the
  profile has this phase, the shot did not reach it" for a phase the shot ran. A logged name now
  matches a profile phase when it equals that name cut to 24 bytes on a character boundary (case
  and runs of spaces ignored), in one place (`domain/phase_names.py`). No stored value changes
  and no shot is derived again.

### Any number about a shot can be asked for in one fixed language, and the chat opens with a line per phase

- **A metric language.** A number about a shot (the cup at the end of the ramp as a share of the
  target yield, the jitter of the scale flow after the first drip, the seconds the pressure spent
  above 6 bar) is written once as a small JSON expression: a channel, a window of the shot (the
  whole shot, a phase by name or number, or a span between anchors such as the first drip or the
  peak pressure), an operation, and optionally what to divide by and what to compare with.
  `POST /api/shots/{id}/evaluate` answers up to 50 expressions with one result each, in order: a
  value, its unit, whether it is measured, estimated or commanded, and a one-line sentence, or an
  absence with its reason (not recorded, phase not reached, no target, never reached, ...), never
  a zero for a sensor the shot lacks. The weight comes from the scale alone: without one it is
  not recorded, never filled from the machine's estimate. A malformed expression is a 422 that
  names the field. Nothing is stored and nothing is judged; it is read-only.
- **The per-phase numbers are computed by it.** Every stored per-phase number that is a window
  statistic is the value of one expression, and its shot-information id is that expression's
  canonical form. No stored number moved on any shot with a pressure sensor.
- **The chat's opening context gains one line per phase.** Every shot in a Set conversation now
  carries, under its warnings, `phase 2 · ramp: duration 16.2 s; ended by Volumetric target; cup
  at end 42.2 g, 117.2 % of target`, so the agent need not ask for the cup at the end of a phase.
  The share is there only when the shot is filed in a version with a target, the cup needs a
  scale, and a log with no phase table has no lines. This moves four items (the phase's name, its
  duration, how it ended, and the cup at its end with its share, which is one item for the chat)
  from extended to base on Settings → Shot information; move them back there if the context is
  too big. The share is no longer an item of its own in any chat tier (the shot page still has
  it). It costs about 30 tokens per phase per shot, and the base meanings about 250 tokens once
  per request.
- **No puck flow without a pressure sensor.** On a machine with no pressure sensor (a GaggiMate
  Standard board) the puck flow, and the numbers built on it, are no longer shown as zero: the
  average, peak and brew flow, the total volume, each phase's flow and volume, and the curve's
  puck-flow, pump-flow, target-flow and water-pumped columns are left out, as every number a
  shot's sensors cannot measure is. The first drip, which is the first puck flow, goes with it. The
  next boot derives every shot again (no data is lost); only shots with no pressure sensor change.

### Each phase is measured, the few plain faults are warned about, and the execution score is gone

- **The score and every band label are retired.** A shot that overshot its target before
  the profile's decline ever ran could still score 10/10: the score only asked whether the
  machine followed its profile, and its bands were calibrated on other people's shots. Gone: the
  1-10 score and its sentence (the shots table's Score column, sort and filter, the shot page's
  score card, the Set trend chart's score series and the version averages, the starting point's
  "executed n/10"), every band label (the resistance level, the adherence, the temperature
  stability and their meanings), and the channeling block (the risk, the four indicators, the
  guidance). A number is a number now, and the shot information says what it measures.
  **Breaking:** diagnostics are recomputed from the raw logs at the first boot (a shot already in
  the archive loses nothing, and no database needs wiping); the two score columns are dropped by a
  migration, which has no down-migration, so take a backup first.
- **Every phase has its own numbers.** What ended it (the next transition's reason; "Unknown" on a
  version 5 log, which records none; a log with no phase table has shot-wide numbers only and
  says so), the cup at its end, what it gained and its share of the target, scale and puck flow,
  the water the pump counted (read until the counter is reset at the weight stop), pressure and
  temperature, where the first drip fell, and each phase's resistance (brew phases), adherence, pre-infusion
  ramp and saturation time and decline taper. The shot
  says which of its profile's phases never began.
- **Four amber warnings, worked out when a shot is read:** over target (the final weight above
  110 % of the filed version's target yield), under target (below 90 %), a phase skipped (the shot
  stopped on its weight or pumped-water target before it began) and fast flow (the scale flow
  averaged over a second above 3 g/s while the pressure stayed at 80 % of the peak or more). None
  is red: without the profile's own statement of intent nothing here can know what is wrong, and
  a **turbo profile will show fast flow** on every shot until a profile can say it is expected.
  Refiling a shot into a version with another target changes its warnings and its shares of the
  target with no re-derivation. The chat reads them first in every shot's base information.
- **What the agents lose, exactly.** From the base information: the execution score and the
  channeling risk. From the extended information, which loses 27 items: the score's confidence,
  reason and penalty components; the channeling block's primary signal, guidance, window
  confidence, flow jitter, flow versus target, pressure drop rate, late flow acceleration,
  pressure jitter, flow spread, flow shape and the per-phase channeling line; the largest
  pressure overshoot and the largest flow overshoot and undershoot; the temperature overshoot, undershoot and stability;
  the pressure area and slope, the flow slope and the weight-rate variability; the resistance
  stability and peak, and the saturation (peak timing) with its band. The processing note, which was
  excluded by default, is gone too. A band that was a base item is now its number, the resistance erosion is the resistance
  slope, and the pre-infusion ramp, saturation time and decline taper stay per phase as plain
  numbers. The extended information gains the per-phase numbers, the agent's rules say a warning
  is a fact to weigh against what the profile is for, and predictions written in old band words
  are graded on the numbers they name. `list_set_shots` filters on resistance level and the two
  adherences as ranges where it took bands, and no longer on the score. The ratio takes the
  version's dose when you typed none.
- **The shipped reference documents no longer describe the retired labels.** The band tables,
  the channeling block and the band calibration notes are cut from the diagnostics reference and the
  threshold-calibration notes (nothing else in them is reworded). An installation picks this up at
  the next boot unless you edited those two documents (`SHOT_DIAGNOSTICS_REFERENCE` and
  `ESPRESSO_PHYSICS_AND_THRESHOLD_CALIBRATION`); an edited copy keeps your text, so cut the same
  passages by hand or reset it with `POST /api/knowledge/docs/{slug}/reset`, which replaces your
  edits with the trimmed text.
- **A field contract.** Each item of shot information answers value, unit, phase, window,
  method and source beside its sentence, and `GET /api/shots/{id}/fields` serves them in order,
  grouped by phase, with the warnings.
- **The shot page is built by phase.** From the fields route, in this order: the facts row, the
  warnings (one amber "phase: fault" line each with its sentence; no card when there are none,
  and never an "all clear"), your judgement, the curves, then a table with a row per phase,
  then the shot as a whole, then the Set and the review as before, and last the context, collapsed.
  The phase table puts what matters first after the phase's name (how it ended, then the cup at its
  end with its share of the version's target when the shot is filed under one, and what it gained),
  so a phone shows it without a sideways scroll; scale and puck flow, pressure, temperature,
  resistance and the firmware analyzer's values follow, and a profile phase the shot never reached
  gets a row of its own, marked "not reached". The banded cards (resistance, profile compliance,
  extraction and weight) are replaced by it: the numbers are the same numbers, and every word on
  the page, the facts row included, is the one the chat reads (the shot time is the server's
  rounding and the ratio uses the version's dose when you typed none). The fields route now serves
  the ratio too. Settings, Shot information lists the new items under the warnings, which lead it
  as they lead every rendering.
- **The shots table's Score column is now Review**, in the same place and on by default. Its badge
  says the shot's first warning as "phase: fault" ("decline: skipped") and "+N" when there are more,
  amber, with every warning and its sentence on hover; a shot with no warning has an empty cell,
  since a missing warning is not a verdict. It sorts most severe first (then a phase's before a
  shot-wide one, then earlier in the shot, shots with none last), and the server does the sorting.
  A column layout or width you saved for Score carries over to Review. The score filter is gone: a
  saved `?score=` link is ignored. The same badge is on each shot of a version in a Set's history
  and in the compare tray, and an open row leads with the shot's warnings.
- **The starting point ranks on your rating alone.** The outcome term lost its execution score
  half, so past outcomes weigh only by rating and rankings can change, not only ties (a Set rated
  4 that scored 5 used to rank below one rated 3.5 that scored 10, and now ranks above it). The
  outcome weighs at most 2 points of the 8 a Set can score, and a version's mean ratio takes the
  version's dose when none was typed, as the shot information and the trends do.
- **The knowledge rules follow.** The 35 seeded rules that explained band labels, and three that
  described the retired channeling thresholds, are removed (a copy you edited stays); the
  pre-infusion rule is now selected by a fast-flow warning or a fast first drip; four rules whose
  signals were bands are reached only by topic through the chat. A tier you chose for the
  renamed resistance item follows it.

### The shots table's Curve column can show the curves you choose

- **Choose the curves and their colours.** The Curve column's heading has a button (Choose curves)
  that lists the nine series the shot page draws, each with a checkbox and a colour swatch. The
  swatch picks from the theme's chart colours and the two text colours, so a choice follows the
  light and dark themes. The last shown curve cannot be hidden, and Reset to default puts back
  pressure in chart-1 and puck flow in chart-2, which is what the column always drew. The choice
  is remembered per browser, like the column choice and widths, and a curve keeps its colour while
  it is hidden.
- **Series that share a unit share a scale.** An actual drawn with its target (dashed) can now be
  compared by eye; temperature is drawn from its lowest value rather than from zero. Pressure
  and puck flow, the default pair, are different units and look as before.

### Making a draft active no longer asks for a stop-condition confirmation

- **Make active is one click for every proposal.** A proposal that moves a stop condition used to
  be refused until you ticked a box, and a proposal that landed as a new profile (a chat proposal
  under a new label, a Set's first recipe forked from a profile) had no box to tick, so it could
  not be made active at all. The check is gone. An edit of an existing profile still lists the
  stop-condition change on its card (a proposed new profile is shown whole, without a
  comparison), and the agent is still told never to change a stop condition unless asked.
- **Breaking, API:** `POST /api/profile-board` no longer takes `acknowledge_stop_changes` (a
  request that sends it is rejected as an unknown field), and a draft no longer has an
  `acknowledged_stop_changes` field.
- **Breaking, database:** a new migration drops the `acknowledged_stop_changes` column from
  `profile_drafts`. It held a yes/no that nothing reads any more; no other data changes.

### Chat answers are rendered as markdown

- **Bold, code, headings, lists, quotes, links and tables show as such** in the agent's answers,
  where `**text**` and `` `text` `` used to appear as typed. Citations (`shot 129`,
  `SLUG#heading`) are still links, including inside bold, lists and table cells. A wide table
  scrolls inside the answer, and a long link or code span wraps instead of scrolling the
  conversation sideways. Raw HTML is never
  shown as elements and images are shown as links, so nothing the model writes makes the browser
  fetch anything. The reason on a proposed change and the text of a proposed insight take bold,
  italics, code and links too.

### The chat sends the extended field meanings only when it reads a shot in detail

- **The shot-field glossary is split in two.** The base fields' meanings stay in the Set and General chat prompts; the extended fields' meanings (about two thirds of the glossary) are no longer sent with every request. They are in what the model is sent exactly once whenever an extended result is: on the newest `get_shot_extended`, `get_shot_full` or `compare_shots` result replayed from the conversation (older copies are left out, and the copy moves to a later result if the budget drops the one that had it), or on this answer's first such read when the conversation has none. An answer with no extended result in view does not pay for them. Settings → Shot information shows the two halves' sizes separately.

### A Set's chat uses the shots it was already given

- **The Set chat is told to use the shots it already has.** The newest shots of the version are in its opening context in their base information, and that context now names their ids; the prompt and the shot tools say that for those shots only `get_shot_extended` adds anything (the diagnostics, phases and curve), and `list_set_shots` is for shots outside the context.

### The chat shows how big the conversation is, and what the cache did

- **The footer says the size, not the bill.** It used to add up the input of every request of every
  answer ("442,137 in"), which counts the whole conversation again for each tool round. It now reads
  `Context 55k of 200k tokens (92% cached)`: the size of the last request, the model's window where
  the provider reports one (Claude Code), and the share of that request the cache served where the
  provider reports a cache. A part the provider does not report is left out, and with no usage there
  is no footer. The running total across the thread is gone.
- **Each answer has a small line of its own**: `10 requests · context 28k → 55k · 9.6k out`. Requests
  are API requests, not the CLI's turn count. Figures are tokens, shown as `842`, `9.6k`, `55k`.
- **Claude Code runs are counted per request.** One request with thinking, text and a tool call used
  to be counted three times, and a run cancelled or killed before its result kept that inflated
  figure. The Anthropic API provider no longer counts a turn's output twice (the stub in the opening
  event plus the final count). OpenAI-compatible cached tokens are now read.
- **The LLM activity panel shows the cached share** beside a call's tokens where known, the chat
  export carries `context_tokens`, `cache_read_tokens`, `cache_write_tokens` and `requests` per run,
  and a new migration adds the cache and context columns to the usage ledger (older rows stay empty).

### Version names are identifiers, and going back moves the Set instead of writing a version

- **Versions are listed by when they were made.** A version's name (v1.2) is an identifier, like a
  tag, not a position: v1.2 can have been made after v2. The log is ordered by when each version
  was made, the version the Set is on is marked as current wherever it sits, and each entry says
  which version it was made from. A prediction can only be compared to a version made before it.
- **Going back puts the Set back on that version.** The button reads "Go back to this version" and asks for an optional note. Nothing new is recorded: the version's
  prediction, shots and outcome are exactly as they were, new shots are filed under it, and a
  recorded outcome is not reopened. The log gets one line ("Went back to v1.2 (from v2.1)") with the
  date and an optional note. The next change continues that version's line: from v2.1 back to
  v1.2, the next minor is v1.3 (or the next free minor), the next major is the highest major plus
  one. Going back takes no prediction. Versions an earlier roll back already wrote keep
  their names and their place. The conversation about the version you went back to is the Set's
  live one again, and tells the agent the Set came back to it, from which version and when.
- **A major version means a different profile, not a newer version of the same one.** Adding a
  version, or accepting a proposal, that moves a Set to another version of the same profile (the
  same entry of the profile list) now starts as a minor change; another entry of the list, or a
  profile no entry holds, starts as a major one. The box is still yours, in both directions.
- **Under the hood:** the version ordinal is gone from the database, the API, the agent's tools and
  its SQL views (which gain `is_current` and `parent_version_label`). Existing databases upgrade in
  place with no data lost; the upgrade keeps every chat, proposal, grade and link.

### The chat is one column, with a badge per Set above it

- **The folder sidebar is gone.** Every Set is a badge above the conversation, as on the Shots page's
  "Chat about" bar (General first, then the Sets with their current version and how many
  conversations each holds). Clicking a badge shows that Set's conversations under the badges, one
  per line (version, title, messages), with **New conversation** at the top; the conversation itself
  now takes the page's full width, and the page's subtitle is gone.
- **Archived Sets are a toggle, not a folder.** **Show archived Sets** at the end of the row (off by
  default) adds one badge per archived Set that has conversations, each listing its own, instead of
  one list of every finished bag's conversations. Opening an archived Set's conversation from a link
  turns it on.
- **The Chat page opens on the newest Set** (the one made last), unless a link or an open
  conversation names another. A first question typed without picking a conversation goes to the
  open badge's Set, and the line above the composer says which.

### Version names are read the way they are written

- **A version name with a leading zero is refused.** The chat's shot search read "01" as v1 and
  "v1.01" as v1.1; it now takes a name only as the app writes it, and its `version` field asks for
  the name as a string, since a number 1.10 arrives as 1.1.
- **The chat heading of a conversation that names no version no longer ends in a space.**

### Find patterns across Sets

- **A button on the Knowledge page's Insights tab asks one model call to find the lessons several
  Sets share.** It reads the confirmed insights of every Set (not those of a Set still being
  designed) with the general insights that already exist and the proposals you declined, and
  proposes general insights, each with the Set insights it was derived from. It is not a chat and
  has no tools, runs only when you press it (never on a timer, after a sync, at boot or from a
  chat or MCP tool), and the page says how many confirmed Set insights arrived since the last run
  and keeps the button off until two Sets have one. Its model is the new `modelPatterns` setting
  (Settings → LLM → Models), falling back to the default model.
- **Approving a proposal deletes the Set insights it came from.** Approve writes the general insight
  (confirmed, scoped so it reaches every one of those Sets) and deletes its sources in one step, so
  each Set's conversations are told the lesson once, as a general one; a proposal can also replace
  an existing general insight, which is deleted with it. A source deleted or taken back since the
  run is left alone and named on the card, and approval is refused when fewer than two Sets'
  insights remain. A proposal that rests on one Set, names an insight it was not given, or states a
  scope that not every source Set matches (or one by profile style) is dropped before you see it and
  counted on the run.
- **Copies of insight text live one run.** A dismissed proposal is kept so the next run is told
  you declined it; when a run finishes, every proposal of every earlier run is deleted (whatever
  became of it) and every earlier run's stored input is blanked. After you delete an insight its
  words can survive in the newest run's input and proposals until the next run finishes, never
  longer; while fewer than two Sets have a confirmed insight no run can start, so they stay until
  one can. Approve and Dismiss are disabled (and refused by the server) while a run is going,
  because the run replaces the proposals when it finishes. Pressing the button again replaces the waiting proposals of the run before. A run
  never stays "running" because of what the model said: an answer with an empty proposal text is
  refused and recorded as a failed run, and anything that goes wrong after the call closes the
  run as failed.
- Migration 0041 adds two tables and one nullable column; no insight is lost and no database needs
  wiping.

### A discarded shot no longer needs a Set

- **Labelling a shot Discard takes it off the "needs a Set" list.** The header's "N need a Set"
  count, the Needs a Set filter and the button that files every waiting shot by its profile all
  leave out a discarded shot, since it counts towards no Set. Keep and Improve leave the shot
  waiting as before. Changing the label back or withdrawing the judgement returns an unfiled shot
  to the list. The shot itself is unchanged: it still shows "needs a Set" in the full list and can
  be filed from there or from its own page. The header count now updates as soon as a judgement
  is saved.

### An insight says what it rests on, and old ones can be replaced or deleted

- **Each Set insight shows the versions it rests on, with their outcome as it stands now.** The
  chat card and the Set page list the versions an insight depends on; when you re-recorded or
  cleared a version's outcome after the insight was written, the old outcome is shown beside the
  new one with a "changed since" mark ("held → failed", "no outcome now"). The agent's insight
  tool takes `rests_on_versions` (versions with a recorded outcome, named like v1.2) beside the
  shots, and an insight must now name at least one shot or one version (one with neither is
  refused, with a sentence saying what to do instead).
- **The agent can propose replacing an insight you added.** The card shows the old text above the
  new and says "Adding it deletes the old insight"; your **Add** adds the new one and **deletes
  the old one in one step**. If the old one was deleted or taken back meanwhile, the new one is
  added on its own and the card says so. Nothing of a replaced insight is kept.
- **The agent can propose deleting an added insight** (new tool `propose_insight_deletion`, Set
  conversations only) with its reason, 20 to 500 characters. The card has **Delete** and
  **Keep**; nothing is deleted until you press Delete, and the insight is still told to every
  conversation of its Set until then. The Set page says which insight has one waiting and links
  into the conversation.
- **Only you remove an added insight, and removed means removed.** No tool, timer or boot step
  deletes one; a deleted insight leaves no retired state, history row or restore, and waiting
  proposals that named it end as "already gone". The conversation that asked is told what you did.
  Deleting a conversation now also deletes the insights it proposed that you dismissed; waiting and
  added ones stay.
- **Old insights weigh less.** Each added insight reaches the agent as a line with its number, the
  version it was learned at, what it rests on and its shots, and the Set prompt asks it to weigh an
  old insight, or one resting on a changed outcome, below the current shots, to look over the added
  insights early in a new version's conversation, and to propose replacing or deleting the few the
  shots no longer support. A `chat-set` prompt you edited keeps your text on boot, so reset it on
  the Prompts page to receive these sections. Migration 0040 only adds columns and a table; no
  insight is lost and no database needs wiping.

### The chat proposes a version's outcome, and insights belong to their Set

- **The agent's grade is a card you answer in the chat.** At the end of its grade a conversation
  about a version proposes one outcome (held, partly held, failed, inconclusive) for the whole
  version, graded against all its counted (Keep or Improve) shots, with the per-claim lines under
  it (new tool `propose_outcome`, Set conversations only). The card has **Accept**, **Record
  another outcome** and **Dismiss**; nothing is recorded until you press one. The Set page shows a
  waiting grade beside the outcome, never as one. Nothing an agent graded reaches a later
  conversation, the experiment log or the track record unless you accepted it, and the agent is
  told at the start of its next turn what you did with it.
- **One press when the grade and the next version come together.** With a grade waiting for the
  current version the agent may propose the next version in the same answer, and accepting that
  version records the grade first, in the same step, as long as you have not recorded an outcome yourself (if you have, it is left alone and the grade stays waiting for its own answer) (the card says "Accepting also records v2's
  outcome"). If you dismissed the grade, the version still cannot be accepted until the version
  is graded, as before. The refusal the agent gets for an ungraded version now says to propose its
  outcome first instead of sending it to the Set page; a profile draft in a Set chat follows the same rule.
- **An insight belongs to the Set it was learned in.** What the agent learns in a Set's
  conversation is stored with that Set and the version the conversation was about, and shown as a
  card with **Add** and **Dismiss** (the link to the Knowledge page is gone). Added, it is told to
  that Set's later conversations only. **An insight placed on a Set no longer reaches other Sets with
  the same bean and grinder.** The Set page lists a Set's insights grouped by the version they were
  learned at (waiting ones with Add and Dismiss, added ones with Take back), with the general
  insights that apply beneath, marked general. The agent's insight tool no longer takes a bean,
  grinder, roast level, process or style scope.
- **The Knowledge page holds general knowledge only**: what you write by hand and agent-written
  insights that could not be placed. General confirmed insights keep matching by attributes and
  still reach every conversation whose Set they match.
- **Breaking: existing agent-written insights about one coffee moved onto its Set, once, at the
  first start.** An agent-written insight (from the chat, or the retired per-shot analysis) moved
  to the one Set it fits, and no longer reaches other Sets on the same bean and grinder. It moves
  only if it is about a coffee: its scope names a bean, or its scope is empty and it lists evidence
  shots (how a Set's conversation stored an insight before this release). It must also match that
  Set's bean, grinder, roast level, process and origin and, when it lists evidence shots, have one
  of them filed in that Set (archived Sets count, Sets being designed do not), and exactly one Set
  may fit. An insight scoped only by grinder, roast level, process, origin or style, an empty one
  with no evidence, one that fits no Set or several, and your own hand-written ones all stay on
  the Knowledge page exactly as they were, still reaching every conversation whose Set they
  match. A moved insight keeps its text, confirmation, evidence and dates and is shown under the
  version of its newest evidence shot (else the version current when it was written, else
  "version not recorded"). Two new migrations (0038, 0039) only add tables and columns; nothing
  is lost.
- The Set chat prompt (`chat-set`) changed: the grade ends with `propose_outcome`, the next version
  may follow in the same answer, and insights are about this Set only. A `chat-set` prompt you
  edited keeps your text on boot, so reset it on the Prompts page to receive these sections.

### The image builds again

- **Fixed: `docker compose build` failed in the front-end stage** with "Cannot find module '../../../tests/fixtures/profiles/…'". The profile summary tests read the same profile fixtures as the Python suite, and the image's type check covers the tests but did not have those files. The front-end stage now copies them; nothing extra reaches the runtime image.

### A new profile is shown as a new profile

- **Fixed: a profile the agent designed from scratch no longer reads as a list of changes from "Empty baseline".** A draft always had a profile it was stored against, so a Set designed with no profile to fork (or a starting point that wrote a new profile) showed every field as a change from a profile you never had, under "from Empty baseline". Such a draft is now marked as a new profile: its card says "new profile" and shows the profile itself (type, temperature, how the shot ends, which is what the last phase stops on, and each phase with its length, pump, transition and what ends that phase) instead of a diff. It no longer warns that it "changes when the machine stops" or asks for the acknowledgement, since there is no earlier profile whose stops it could change, and it never lands on an existing board profile. Drafts you already have that were designed from scratch (those based on the empty baseline, and the starting point's own profiles) are marked on the next start, and their stop-condition warning is cleared with it. The empty baseline is no longer listed among the profile versions, and neither the agent nor the starting point is offered it as a profile. Edits of a real profile keep their diff.

### Profile list (the Profiles page)

- **The Profiles page is one list.** Every profile you have had is a row with two switches,
  **On the machine** (the next sync puts it there or removes it) and **Starred** (the machine's
  home-screen carousel; kept while the profile is off, applied only while it is on), what it
  brews, where it stands on the machine and what the next sync will do about it. Profiles that
  are off are hidden unless you ask ("Show profiles that are off"). Switching off a profile a
  Set brews, or the one the machine has selected, asks first. With writes off the page says the
  machine will follow when they are on.
- **A row opens to the profile's versions**, newest first: when each was made, where it came from
  (the agent, an edit, the machine, edited on the machine, an import), its shots and the Sets
  that brew it, **Make active** (any version, the first included) and **Edit a copy**. The
  first version shows a summary; every later one shows what changed from the version before it.
- **A change that keeps a profile's name is a new version of that profile, whoever made it.**
  A change finds its profile through the profile's version list (new migration `0037`
  records who made each draft), whichever version it is based on, so editing an older version
  no longer produces a proposal that cannot be added. The agent's changes (`draft_profile`,
  refine, the starting point) are versions of the profile they were based on the same way. The
  firmware's own profiles and ones made on the display are no exception: the new version keeps
  the exact name, with no `[AI]` suffix, and the sync saves it under that name and replaces the
  old file. A name never changes through a version: a changed name, or a profile written from
  scratch, is a profile of its own and gets the `[AI]` suffix, so the suffix only marks profiles
  the agent wrote, and who made a version (you or the agent) is shown on the version. A hand edit
  is labelled as yours, not the agent's. The rule lives in `db/repos/lineage.py`; proposals
  made before this one keep landing as they did.
- **Proposals are independent candidates.** Making one active never blocks or undoes another
  (the "a newer draft is waiting" refusal is gone), and a proposal based on a version that is
  no longer active, or was never pushed, still lands on its profile, found through the
  profile's version list instead of the version it is on now.
- **Proposals live inside the profile**: a version the agent (or the JSON editor) proposed is
  marked **Proposed** above the versions, with **Make active** (which records the Set's version as
  the old Put on the board did) and **Decline**. A proposed new
  profile is a row marked **New**. Links that used to land on `#staged` open the newest
  proposal's row.
- **Conflicts**: a profile whose file was edited on the machine shows a **Conflict** badge and
  opens on the app's version and the machine's side by side (two columns from tablet width),
  with **Keep the app's** and **Keep the machine's**. If the machine's file changed again since
  you looked, nothing is done and the panel shows the new one.
- **The reset banner's lines account for its numbers**: a machine file matched to a profile
  that is off says the next sync removes it.
- **The reset question moved here**: when the machine looks reset, one banner asks "put back N
  profiles and remove M?" with one button; the Sync page's Resume banner is gone and points
  here. The Writes switch's confirmation now says what a sync does (it removes profiles that
  are off, the machine's own included, and leaves conflicts to you).
- **Removed**: the "yours / the app's" split, Take, Go back a version, Delete, "Waiting for
  you", "Deleted, still on the machine", "On the machine, not on the board", the Versions table
  and the draft status badges, and the routes behind them (`POST /api/profile-board/take`,
  `.../go-back`, `DELETE /api/profile-board/{id}`, `PUT .../home-screen`; the star is
  `PUT .../starred`). The board read no longer carries `go_back_blocked`.
- A profile uploaded from a file now appears in the list, switched off (or as a version of the
  profile that has its name), so importing never changes what a sync does to the machine.
- The board read carries each profile's active version document, so a row can say what it brews.

### Profile list (back end)

- **Breaking in behaviour: a sync with writes on now removes profiles that are switched off,
  including the firmware's own and ones made on the display.** The board is now a list of every
  profile you have had, each switched on or off the machine, and a sync makes the machine hold
  exactly the ones that are on. The only guard on a removal is a fresh load that must hold what
  the archive last recorded. A profile changed on the display to something it never had is never
  overwritten or deleted: it becomes a conflict you settle (`POST /api/profile-board/{id}/conflict`),
  and nothing is done for it meanwhile; an older version put back on the display is no conflict.
  Migration 0036 adds the switch and each profile's version list; at the next boot the list is
  filled once from everything stored, so old developing profiles, imports and deleted rows come
  back as profiles that are **off**, with their versions. Nothing is deleted from the archive.
- **New routes**: `PUT /api/profile-board/{id}/on-machine`, `.../starred`, `.../active-version`,
  `GET .../versions`, `GET` and `POST .../conflict`. The board read gains, per profile, the
  switch, the active version, the Sets that brew it, proposals waiting for it and any conflict,
  and, while paused, what resuming would do.
- A Set that brews a profile no longer keeps its file on the machine when the profile is
  switched off or replaced; the board read lists those Sets so a page can warn first.

### Sync with machine

- **Changed: "Pull from machine" is now "Sync with machine".** The button on the Shots page,
  its section on the Sync page and every line that told you to pull (the Profiles page, the
  Writes switch, the Sync page, Settings, the README and the chat agent's instructions) now say
  sync. The Sync page's first section is now reached at `/sync#sync` (`#pull` still works); the API
  routes, stored settings and the other names are as they were.
- **The message after a sync now says what it did to the profiles.** After the shots ("Synced: 2
  new shots.") it says how many profiles were read from the machine and what was written to it:
  "Read 9 profiles from the machine; wrote 2 (pushed 1, removed 1)." Writes are counted per
  profile pushed, removed or starred, not per request to the machine. With writes off it says
  "no writes (writes are off)"; with nothing to do, "no writes needed"; after a suspected reset,
  that writes are paused; a profile already on the machine as it should be is counted apart
  ("1 was already on the machine"); and when something failed it says "nothing written, 1 failed"
  rather than that nothing was needed. The toast waits for the sync's profile pass however long
  it takes. The profile pass of a sync now records
  how many profiles it read, on its run in the sync ledger.

### Profiles reach the machine only through the profile list

- **Breaking: the staged push is gone.** `POST /api/profile-drafts/{id}/push`,
  `.../rollback` and `.../approve` are removed (a request to one is a 404), and with them the
  Approve, Push and Roll back buttons, **Stage as is**, and the Profiles page's writes banner.
  The only way a profile reaches the machine is the profile list: make a proposal active and
  the next sync (with the Writes switch on) puts it on the machine. No database change needs
  undoing, and no data is lost: drafts, Set versions and the write audit keep what they held.
- **One click makes a proposal active.** **Make active** approves the proposal in the same
  action (`POST /api/profile-board`), carries the Set and the
  **Major change** choice, and a refused request leaves the proposal as it was.
- **No two profiles in the list share a name.** Making active a proposal that would add a
  second profile beside one with its name (or rename one onto a name another holds) is refused,
  and the proposal says which profile has the name and to use Edit a copy there. A proposal
  whose exact document is already in the list says so instead of offering a request that would
  fail. The first sync still takes the machine as it is and lists profiles that share a name
  instead of refusing them.
- **Changed: the chat and Set prompts and tool notes** say the person makes a proposal active
  on the Profiles page and the next sync puts it on the machine (no approve or push step), so
  the agent no longer describes one.

### A quick Claude Code install still says how it went

- **Fixed: Settings → LLM could miss the "installed" message.** Starting an install answers at once, and when the download finished before that answer was put together it said "done" already, so the page never saw the install end and showed no message. The answer is now always the install as it started, and the page reports the outcome when it sees it finish.

### One Writes switch in the top bar

- **Changed: writes to the machine are switched on and off from the top bar.**
  The switch sits beside the machine status on every page and says **Writes on**
  or **Writes off** (just **on** / **off** on a tablet, and only an icon on a
  phone, where the brand name in the header gives way too). Off is the
  default and turning it off is immediate; turning it on asks first and says what
  it means (see the profile list above). A change the server refuses
  (for example when sign-in is on and you are signed out) says so under the
  switch and leaves it as it was. The **Writes** card on Settings → Machine
  access is gone, and the Profiles page banner, the refusal messages and the
  README point at the switch instead. The stored setting and the API are
  unchanged.

### The sync makes the machine hold the profiles that are on

- **With device writes on, every sync ends by making the machine match the profile list**
  (routes under `/api/profile-board`). The first such sync takes the machine's profiles into the
  list as they are (the home screen is each profile's star) and writes nothing. After that a
  sync saves a profile's active version the machine does not hold, after running it through the
  safety policy with the current bounds and reading what came back against what was sent;
  removes the file a newer version replaced and the files of profiles that are switched off,
  the machine's own included; and sets each profile's star. With writes off a sync only reads.
- **A removal is guarded by a fresh load:** the file must still hold exactly what the app last
  recorded, and never a file another profile or a Set still stands on, and never the selected
  profile while no other is on. A file edited on the display is not overwritten or removed
  unseen: it becomes a conflict you settle. If a sync stops halfway, each profile is left old
  or new and the next one finishes it. A version that does not read back as sent is removed
  again and the previous version stays; it is not tried again until another version is made
  active.
- **A machine that looks reset** (none of the files the last sync left is on it) pauses writes
  until you resume them (`POST /api/profile-board/resume`).
- **A sync reads before it writes.** A profile that already holds exactly the content to push
  is reused and nothing is saved. Otherwise the new file is saved and read back, the star and
  the selection move to it if the old one had them, and then the old copy is removed. A
  profile's file is found by content, so the same profile under another id is not a change. The
  firmware clears its startup-profile setting when that profile is deleted and this app never
  writes settings; the sync's summary says when that happened.
- **A Set's profile stays its own.** A version that changes only the grind, dose or yield, a
  Set rollback and an accepted proposal keep naming where the profile is on the machine, and a
  proposal for a Set finds its profile by looking back through the Set's versions. A file a Set
  still brews is not removed while that Set's current version brews it.
- **Reading the list is cheap by default** (served from the last mirror); add `?live=true`
  to read the machine now.
- **Sync runs carry a summary** of what the write phase did (added, conflicts, recorded,
  pushed, removed, left on the machine, home-screen changes, failures), and every action is a
  sync event and a row in the device-write audit.
- **Schema (new migrations, no reset needed):** the list and its markers, and a summary column
  on sync runs. No existing data changes.

### Adherence is judged on what each phase steers by

- **Fixed: a pressure profile no longer shows a flow adherence.** The machine
  logs a pressure target and a flow target on every phase that drives the pump,
  but only one of them is the target: the other is a limit. The shot page, the
  shot information and the execution score compared the measured flow with the
  logged flow target in every phase, so a profile that steers by pressure
  everywhere (every brew phase of the real shots in the archive's fixtures)
  showed a **POOR** flow adherence and a flow penalty of up to 1.4 points that
  meant nothing. Adherence verdicts now judge each phase only on what it was
  steering by, read from the profile the shot was brewed with: pressure over the
  phases that steer by pressure, and **pump flow** (what the machine's flow mode
  controls, not the flow through the puck) over the phases that steer by flow. A
  simple power phase and the few seconds the machine keeps recording after the
  shot are never graded, which also stops the largest pressure overshoot from
  reporting the pressure falling after the shot as an overshoot. Each phase in
  the Phases line and the shot page carries only the adherence it is graded on.
- **A moment the limit was in charge is not graded either.** The machine drives
  the pump by the lower of what the pressure and the flow ask for, so a pressure
  phase whose pump flow sits at its flow limit (or a flow phase whose pressure
  sits at its pressure limit) shows the limit working, not a missed target, and
  is left out of the adherence. The pump flow the machine logs lags the pump by a
  third of a second, so the first samples of a climb to a limit count too. A phase
  needs three graded samples for an adherence of its own, as a whole shot does. On
  the constructed profiles for the fixture shots this moves the pressure
  adherence of one shot from 2.32 to 2.20, another from 2.25 to 2.04, a third
  from 3.20 to 3.10 and the export's from 0.86 to 0.47 (FAIR to GOOD). A phase
  the limit held throughout simply has no adherence of its own; only a shot with
  fewer than three graded samples in all is "not graded", which drops that
  penalty and lowers the score's confidence to medium.
- **Scores of pressure-profile shots rise, by up to 2.4 points.** The flow
  penalty (up to 1.4 points) was fake, and the pressure penalty (up to 1.0) now
  counts only the samples the target, not a limit, was steering: the export
  fixture's shot goes from 7.7 to 9.3. A profile with no flow-steered phase has
  nothing to grade for flow, which the shot page says ("not applicable") and
  which neither costs points nor lowers the score's confidence.
- **A shot with no known profile shows no adherence.** Without the profile there
  is no telling which target was real, so the shot is not graded rather than
  graded wrongly: no adherence lines, no adherence review tokens, and the score's
  confidence drops to medium, as it does for any missing measurement. Such a
  shot loses both adherence penalties (up to 1.0 point for pressure and 1.4 for
  flow), so its score can be up to about 2.4 points higher than the same shot
  graded against its profile, at medium confidence: compare scores only between
  shots that were graded. This is every imported shot whose profile the archive
  never held, and any shot the machine's profiles were not mirrored for (Sync
  page, Sync with machine). Profiles are taken as they were mirrored: the archive
  does not check them against the machine. When the profile mirror links a shot
  to a profile, or an import's profile-name match does (a shot with no profile
  of its own, matched against the profiles the archive holds), the shot is
  recalculated at once. Importing a profile on its own links none of the shots
  already in the archive.
- **Fixed: an undershoot is 0 when the shot never fell below its target.** The
  largest pressure, flow and temperature undershoot was worked out so that a
  shot sitting above its target throughout showed its smallest overshoot as an
  undershoot (an overshoot of 0.8 bar with an "undershoot" of 0.1). Such a shot
  now shows 0. Shots that really dipped below target show what they did before.
- **Fixed: the channeling check's flow-against-target reading only counts flow
  phases.** The channeling indicators compared the flow through the puck with the
  logged flow target in every phase, but in a pressure phase that number is a
  limit (or 0), not something the machine steered by. The reading now uses only
  the samples of phases that steer by flow (not those a pressure limit was
  holding); for a pressure profile, or a shot with no known profile, it is "not
  applicable" instead of a number, and the channeling risk weighs the pressure
  jitter in its place, as it always has when no flow was commanded.
- **Every stored shot is recalculated once, at the first start after the
  update,** as for the resistance change above: scores, verdicts and the
  Phases line move, notes, judgements, Sets and curves do not.

### The machine's passwords are no longer kept

- **Fixed (security): the archive stored the machine's Wi-Fi, access-point and
  Home Assistant passwords in plain text.** GaggiMate firmware returns them in
  its settings document, and the archive kept that document whole, so the
  passwords sat in the database, were served by `GET /api/machine` to the web
  and were copied into every backup. Secrets are now removed before anything is
  stored or shown: the three known passwords and any key whose name contains
  `password`, `token` or `secret`, at any depth. The first start after the
  update also removes the passwords from what is already stored, without
  touching the rest of the archive.
- **Backup files written before this update still contain the passwords.**
  Delete them or keep them somewhere only you can read, and change the Wi-Fi,
  access-point and Home Assistant passwords on the machine if the archive or a
  backup has left your own network.

### Only profiles are written to the machine

- **Removed: sending notes to the machine and cleaning up its storage.** The
  Sync page no longer has the **Send notes to the machine** and **Clean up the
  machine's storage** sections, and the routes behind them are gone. The only
  thing this box ever writes to the machine is a profile, pushed from the
  Profiles page; it never deletes a shot from the machine, never writes a
  judgement to a shot's notes card and never writes a device setting. The
  machine's notes cards are still read on a sync and seed a judgement once.
- **The machine's own rotation deletes its oldest shots when storage runs low,
  and that is accepted.** The firmware does this whether or not this box has the
  shots, so sync from the Sync page often enough that nothing waits on the
  machine for long. `docs/device-gotchas.md` says which firmware routine it is.
- **Breaking: the cleanup history table is dropped.** A new migration deletes
  the per-pass ledger of past cleanup runs (the per-shot record stays: every
  delete is still in **Recent writes** and the shots it removed are still marked
  as gone from the machine). Nothing else is lost and no reset is needed.
- **Removed settings:** `deviceCleanupMode`, `deviceCleanupKeepNewest`,
  `deviceCleanupMinFreeKb` and `notesWritebackFields`. Their stored values are
  deleted by the same migration, and a `PATCH` naming one is refused like any
  other removed setting; their `GAGGICLANKER_*` variables join the list an old
  compose file is told is ignored.
- **Recent writes still lists what older versions wrote.** Rows of the two
  removed kinds (`shot_delete`, `notes_save`) stay in the audit as history.

### Shots brewed after a machine reset are archived

- **Fixed: after the machine's settings were erased (a reflash, a factory reset,
  a replacement board), the shots it brewed were never archived and they
  overwrote the ratings, volumes and notes of older shots.** The machine
  numbers its shots from a counter kept with its settings, so it started again
  from 0 and used numbers the archive already held for different shots. A sync
  saw the number, believed it already had the shot, skipped it, and copied the
  new shot's rating, volume, temperature, pressure, flow and notes onto the old
  one. A shot is now identified by its number together with the time it
  started: a new shot under an old number is archived as its own shot, the old
  shot keeps everything it had and is shown as no longer on the machine, and a
  JSON export of a new shot under an old number is imported as a new shot
  instead of replacing the old one. Shots already overwritten are not repaired
  (the sync log's "the device's index entry changed" lines say what changed; a
  backup from before is the clean source). A new migration rebuilds the shots
  table to carry the new key; no rows are lost.

### Puck resistance comes from the machine

- **A shot's puck resistance is now the machine's own measurement whenever the
  shot has it.** The firmware records `pr = sqrt(P) / Q_puck` for every sample
  (the real shots in the archive's fixtures, from older firmware, already carry
  it); its square is the same quantity as our `pressure / flow²` on the same
  scale, so the bands, the score's erosion penalties and the rule texts keep
  their meaning and their numbers. Ours is the fallback, for a shot with fewer
  than three valid machine readings (a board without a pressure sensor, or a
  phase where the machine's estimate has not started), and the shot page and
  the shot information now say which one a number is. On the four real shots
  the two agree closely (level, stability and
  erosion bands identical); peak resistance and its timing follow the machine's
  estimate, ramp spikes included, and the saturation band of one flat hold
  moves from good timing to early.
- **Every stored shot is recalculated once, at the first start after the
  update,** from the shot file the archive keeps, so a search, a sort or a Set
  never compares two definitions of resistance and the execution score. It takes
  a second or two per few hundred shots and is logged (`shots_rederived`).
  Notes, judgements, Sets and the curves are untouched. A new migration records
  which version of the calculation wrote each shot, so a later change to it is
  brought to the archive the same way.
- **The shot page and the shot information also show what the firmware's own
  shot analyzer shows.** The machine's puck resistance (s·√bar/mL) and the
  liquid resistance (bar·s/mL), as the firmware reports them (average weighted
  by time, with the first, last, lowest and highest reading), appear under a
  "Firmware analyzer" heading at the bottom of the Puck resistance card for the
  whole shot and in a column of the Phases table for each phase; the water the
  pump moved and its difference to the beverage weight appear in the
  Extraction and weight card. They are not banded and not the resistance level
  above them, so nothing scores or filters on them. In the shot information
  they sit in the extended tier: what a chat is told about a shot by default
  does not change, and the glossary every chat carries grows by about 300
  tokens. The water shows only for machines whose pump counts it (a shot file
  of format version 7 or later whose counter rose; a board without a dimmed
  pump records zero, which is left out rather than shown as 0 ml, where the
  firmware's own page shows 0). Shots already in the archive get them at the
  next start.

### Reviews use the resistance, temperature and channeling rules

- **Fixed: a shot review never selected the band rules for a synced or
  imported shot.** The rules for puck resistance level and erosion, temperature
  stability and channeling risk are chosen by signal tokens, and a real shot's
  diagnostics named those under short keys (`level`, `erosion`, `stability`)
  that no rule matches, so of the diagnostics band rules, reviews drew only on
  the pressure and flow adherence ones. The tokens are now named by
  section (`resistance_level`, `temperature_stability`, `channeling_risk`, …)
  for every shot, and the free-text notes and channeling guidance are no
  longer passed off as tokens. Reviews already written are unchanged; a new
  review of an old shot picks the rules up.
- **Fixed: a shot review's background excerpts no longer chase healthy
  readings.** With the tokens named by section, a low channeling risk, minimal
  temperature overshoot or a very stable resistance became a search for prose
  about the normal case, and the first of those took the one slot the
  diagnostics reference gets, crowding out the excerpt about what does stand
  out. Whether a reading is healthy is now decided per metric (`LOW` is fine
  for channeling risk and worth a search for resistance level), next to the
  band tables, and a test refuses a band label nobody has classified. Anything
  about the puck or the recipe, such as a low or high resistance level, a
  moderate or steep decline of resistance or a channeling risk above low, is
  still searched for. A gradual decline of resistance (how a bed settles) and a
  channeling window too short to judge count as unremarkable.
  Reviews already written are unchanged.
- **A shot review's background excerpts now look up what stands out about the
  puck first, in the knowledge base's own words.** The bands that stand out
  were searched in alphabetical order, so how well the machine followed the
  profile took the diagnostics reference's one slot before the puck did, and
  "resistance level LOW" matched the summary section rather than the one on
  puck resistance. The puck (resistance level, erosion, stability, saturation,
  channeling risk) is now searched first, then temperature, then how the machine
  tracked the profile, then trends, and a low or high resistance is asked as
  "low puck resistance". The puck resistance section now reaches reviews of
  shots with a low or high resistance. Reviews already written are unchanged.

### Tested against GaggiMate firmware v1.9.0

- **gaggiclanker is now tested against GaggiMate firmware v1.9.0.** Nothing
  changes for the machine's own data: shots, profiles and notes are read and
  written the same way. The firmware simulator behind the opt-in tests now
  builds the v1.9.0 release, pinned by `GAGGIMATE_FIRMWARE_REF`.

### Download a conversation's message log

- **The Chat page can save a conversation as a file.** **Download log** in the
  conversation's header saves one JSON file: the messages in order, the agent's
  tool calls with their arguments, what the tools answered, and a record per run
  with the model, tokens and error. It does not hold the context the app injects
  into the agent each turn (the Set's ledger and recent shots, the glossary),
  and the answer of a tool that renders shots (or one the provider could not
  pair with its call) is replaced by its size; every other answer, including
  refusals, stays whole. The API route is `GET /api/chat/threads/{id}/transcript`,
  deliberately not `/log`: content blockers drop `/log?…` requests, which showed
  as "Failed to fetch". If a blocker still stops it, the button now says so.

### Minor versions for dialling in, a new major for a new direction

- **A Set's versions are named v1, v1.1, v1.2, v2.** A grind, dose or yield
  change, or a profile draft that only tunes a parameter, is a minor version:
  one click finer on v1 is v1.1, not v2. A functional change to what the
  profile does is the next major. You decide on each: the Add a version form,
  a change card and a draft's push for its Set have a **Major change** box, and
  the button says which version it records ("Accept as v1.1", "Accept as v2").
  The box starts where the rule puts it — a different profile is major,
  anything else minor — and the agent may suggest major on its card, with a
  reason shown beside the box; it never decides.
- **Existing versions keep their numbers.** The upgrade adds the new name
  beside every version as N.0 (shown as vN), so every "v3" already written in a
  chat, a prediction or a note still means the same version. Nothing needs
  resetting.
- **The name is used everywhere a version is named**: the Set page and its log,
  the version pickers, the Chat folders and headings, the "Chat about" bar and
  its question, the Set badges, the proposal and draft cards, the toasts, the
  agent's opening context, its tools and their refusals, and the SQL views
  (`version_label` in `v_set_versions`, `set_version_label` in `v_shots`).
  The agent's shot search takes a version by its name ("v1.1", "1.1", or "2"
  for v2). The message an accept sends to the agent reads "Accepted: your
  proposed change (Grind 2 → 1) is now v1.1 of this Set."
- **If you edited the Set chat's prompt** (Settings → Prompts, `chat-set`), your
  text is kept and does not receive the new paragraph on version names; reset
  it to pick that up.

### Review replaces the per-shot analysis

- **Review on a shot's page** asks a model to read that one shot, and nothing
  else starts one: no chat tool, batch, timer or sync step. It is handed the
  shot's own information (everything the shot tools can show, whatever your
  Settings → Shot information choices, except your judgement, the note typed on
  the machine, the Set version's recipe and which Set the shot is in), the
  profile it brewed, its detected style, and the knowledge rules and excerpts
  its telemetry selects. It never sees your judgement, the Set, its versions,
  another shot or an insight, so its taste prediction is blind.
- **It writes three things to the shot and does nothing else**: what it expects
  the cup to taste like (balance, body, confidence), one paragraph describing
  what the telemetry shows and why, and a one-sentence summary. It proposes no
  change, insight, profile edit or question. The card shows the summary first,
  the prediction beside your own balance, the description, the rules and
  excerpts it cited (linked to the Knowledge page), the model and the time, and
  **Review again**, which keeps the earlier reviews stored and shows the newest.
  Nothing about a review appears in the shots table, its open row or the Set
  page.
- **The chat reads a review as shot information.** A new **Review** group under
  Settings → Shot information (predicted balance, body and confidence, the
  description, the summary, when it was written and by which model), all at the
  extended tier, so `get_shot_extended`, `get_shot_full` and `compare_shots`
  carry it and you can move it like any other item. The glossary says each line
  was written by a model from the shot's data without your judgement, and the
  chat's rules gain one: weigh a review below the measured numbers and your
  judgement, and never treat it as evidence for a Set change on its own.
- **Settings**: `modelAnalysis` is now `modelReview`, and the knowledge budget
  `analysisChunkTokenBudget` is now `knowledgeChunkTokenBudget` (the starting
  point reads it too); a value you had set moves with it. The LLM page's
  budget card is called Knowledge.
- **The API**: `POST /api/shots/{id}/reviews` (202 with a `running` row;
  `?wait=1` for scripts), `GET /api/shots/{id}/reviews`, `GET /api/reviews/{id}`
  with the input the model was given, and `reviews` on the shot detail. The SQL
  tool reads `v_reviews`. A draft is made from notes (or a whole document);
  `analysis_id` and `suggestion_id` are no longer accepted.
- **Breaking: the per-shot analysis is gone, and so is what only it used.** A
  new migration carries every finished analysis into a review with the same id
  (its taste prediction as it was, its diagnosis as the description, an empty
  summary) and drops the rest: every suggestion (open, accepted or rejected)
  with its accept and reject routes, the execution notes, profile patches,
  questions and the rest of each analysis's answer, failed and interrupted
  analyses, and the `analysis` and `analysis-user` prompts — **a prompt you had
  edited is lost**; the new `review` and `review-user` prompts start from their
  shipped text. Also gone: the Set page's **Analyse the un-analysed** with its
  large-batch question and its Suggestions card, the `run_analysis` chat tool,
  drafting a profile from an analysis, the analysis state in the shots list and
  its Flags badge, and the shot page's analysis panel. Set versions an accepted
  suggestion made still say "From an analysis", insights an analysis proposed
  keep saying so, and older drafts keep their link; no reset is needed.
- **If you edited `fragments/chat-rules`, your text is kept and the new rule on
  weighing a review never reaches the chat.** Reset it under Settings → Prompts,
  or copy the paragraph that starts "A SHOT'S REVIEW IS A MODEL'S READING".

### An accepted change says what to do at the machine

- **The accepted change card names the next step.** It used to say only
  "Accepted as v2", so after a grind change a person looked for a push that
  does not exist. It now says what you do by hand, from the change itself: for
  a grind, dose or yield change, "Nothing goes to the machine: the profile is
  unchanged, so there is nothing to push. Set the grinder to 1 and brew; the
  next shots on this profile are filed under v1.1 by themselves." A profile change
  says to select that profile on the machine. The line under a waiting card
  says the same before you press Accept.
- **The agent knows it too.** The Set conversation's instructions now say that
  an accepted change puts nothing on the machine, that there is nothing to
  push, approve, stage or log for a grind, dose or yield change, and never to
  describe a step the archive does not have. The "Accepted:" message names the
  change: "Accepted: your proposed change (Grind 2 → 1) is now v1.1 of this
  Set."
- **The agent can no longer propose switching to a profile the machine does not
  have.** Nothing in the app can put an existing profile version on the machine
  (only drafts are pushed), so a version naming one could not be brewed; the
  agent is told to make a profile draft instead. `list_profiles` now says which
  profiles are on the machine (`on_machine`).

### Chat about a Set from the shots table

- **A "Chat about" bar above the shots table** has one button per Set you are
  brewing (not archived, not being designed), each labelled with the Set's
  current version ("Guji on the Niche · v4"). It opens or continues that
  version's conversation on the Chat page with a question already typed ("I've
  judged my latest shots on v4. What do they show, and what should I change
  next?"): judge the shots in the table, press the Set's button, press Enter, or
  replace the question with your own. The bar is hidden when no Set is active.

### The chat sees every shot field, and knows what each one means

- **A Set conversation opens with its version's newest shots in full base
  information**, not one summary line each: identity and whether it counts,
  shot time, yield, exit reason, execution score, first drip, peak pressure,
  average brew flow, resistance level, channeling risk, pressure and flow
  adherence, and your whole judgement. How many is the new setting
  **`chatRecentShots`** (Settings → LLM → Chat; default 20, at least 1). The
  compared-to version's shots are no longer listed one by one; its side is still
  in the evidence table and the gold standard, and any shot is a search away.
- **Three shot tools.** `get_shot` returns a shot's base information,
  `get_shot_extended` everything else (the execution score's working,
  temperature, pressure, flow and weight statistics, every channeling indicator,
  profile compliance, one line per phase and the curve) and
  `get_shot_full` both. `get_shot`'s `detail` argument and the analysis it used
  to carry are gone. `compare_shots` renders each of its shots in full, in the
  order given.
- **`list_set_shots` is now a search** over the Set's shots: by version, label,
  balance, dates, a range on shot time, yield, first drip, peak pressure, average
  brew flow, execution score, rating, dose in and out or ratio, or a band of
  channeling risk, resistance level, pressure or flow adherence; sorted by date or
  any of those numbers; at most ten results, each in its base information.
- **The chat is told what every field means.** The Set and General prompts carry
  a glossary generated from the same catalogue the shots are rendered from: what
  each field measures, its unit, which way is better, and every band label with
  its threshold. A value the machine did not record is left out rather than shown
  as zero. The chat rules also gain two the per-shot analysis had: the execution
  score is computed, not the agent's to give; and two aligned channeling
  indicators mean a channel, one means noise.
- **If you edited the `chat-set`, `chat-general` or `fragments/chat-rules`
  prompt, your text is kept, so the glossary and the two moved rules never reach
  the chat.** Reset the prompt under Settings → Prompts to take the new text, or
  add `{{shot_fields}}` after `{{> chat-rules}}` in the first two and copy the two
  rules into the third.

### The curve the chat reads keeps its shape in about sixty rows

- **A shot's curve is no longer handed to the chat sample by sample.** The
  machine logs four samples a second, so a shot's curve was some two hundred
  rows and over 2,000 tokens for the default channels. The chat now reads about
  sixty rows chosen to keep the curve's shape (largest-triangle-three-buckets on
  pressure and puck flow), and whatever that number, it always keeps the first
  and last sample, each phase's first and last, peak pressure, first drip and
  both ends of the largest pressure drop, each found by the diagnostics
  engine's own rule, so a short pressure drop is never stepped over. Every
  channel is cut at the same moments. The table says how many of how many
  samples it holds and what was kept; a shot no longer than the budget is sent
  whole. On a typical 213-sample shot the curve drops from about 2,200 tokens to
  about 620, and the shot's whole extended information from about 2,900 to
  about 1,400.
- **How many rows is a new setting, `chatCurvePoints`** (Settings → LLM → Chat;
  default 60, at least 10). It is a target: the moments above are kept whatever
  it is, so a shot can come to a few rows more. Settings → Shot information shows
  it beside the extended estimate, and its estimates and curve examples follow
  it.
- **A curve channel moved into base now reaches a Set conversation's opening
  context and the shot search.** Both used to leave it out while the settings
  page counted it in their estimates; they now read the shots' samples exactly
  when base carries a curve, and not otherwise.

### Settings → Shot information: choose what the chat is told about each shot

- **A new settings page, Settings → Shot information**, lists every item a
  shot carries, one table per group, with what it means, a **base | extended |
  excluded** control (the default marked) and its value on a real shot of your
  archive: the newest shot you judged, else the newest shot. The value is
  written by the same renderer the chat reads, so the page shows exactly what
  the agent sees. Shot id, Set version and "counted" are locked in base: the
  agent cannot search or cite a shot without them.
- **Every click saves, and applies from the next chat turn**: the opening
  context, the three shot tools, the shot search and the glossary all read the
  tiers per turn, in the chat and in the `claude_code` provider's tool server
  alike. An excluded item is left out of all four; a General chat's SQL tool
  can still read the archive's views.
- **The page shows what a choice costs** in approximate tokens, measured on
  that shot: base and extended per shot, the glossary, and what a Set
  conversation spends on every turn opening with its newest shots (base times
  `chatRecentShots`, which links to Settings → LLM → Chat). **Reset to defaults**
  puts every item back, after an inline confirm.
- Only the items you moved are stored (a new migration adds one table and
  changes nothing that exists: no reset), so an item a later release adds, or a
  default it changes, reaches you unless you chose otherwise.

### The agent hears when you accept or decline its card

- **Accept on a card in the chat now tells the agent.** It used to record the
  version and say nothing to the conversation, so the agent went on as if the
  card were still waiting. The button now sends a message as your next turn
  ("Accepted: your proposed change is now v4.1 of this Set.", or "your
  first recipe is now v1"), held until an answer still being written has
  finished. **Decline** does the same with your reason ("Declined: <reason>",
  or "Declined: no reason given."), and the agent answers it in the same
  conversation without proposing that card again. Answering on the Set page
  sends nothing.
- **One conversation is one version, and the agent says so.** When a card is
  accepted it tells you directly to start a new conversation (New under the Set,
  or Discuss in chat) to brew and analyse the new version. That includes the
  design: the conversation a first recipe was designed in no longer turns into
  the Set's ordinary one, and Discuss in chat on version 1 opens a new
  conversation instead of returning to the design.

### A large Set batch asks before it spends

- **Analyse the un-analysed asks first when it would run more than ten
  analyses.** The Set page shows how many shots it would analyse (one provider
  call each) and roughly how long that takes, with a button to go ahead and one
  to cancel; ten or fewer start at once as before. The guard is on the server:
  `POST /api/sets/{id}/analyse` refuses a batch of more than ten with a 409
  `LARGE_BATCH` (`details.count` is the size) until the body says
  `acknowledge_large_batch: true`. Shots already being analysed are not counted.

### A shot keeps its yield when the scale drops to zero as it ends

- **A shot whose scale reads zero for its last few samples keeps the yield
  from right before the drop.** The machine stores the scale reading at the
  moment it closes the shot's file, so a cup lifted, or a scale that resets,
  in the last second left the shot with no final weight although its curve
  showed the full yield. The pattern is matched narrowly: the zeros last at
  most the three seconds the machine keeps recording after a brew, and the
  reading before them is at least 1 g and within 90% of the shot's highest.
  A shot that reaches zero any other way still has no final weight.
- **Shots already in the archive are fixed at the next start.** The app
  re-reads the stored file of every shot that had a scale connected but no
  final weight, and fills the weight where the rule finds one (the log line
  `boot_reconciled` counts them as `final_weights_refilled`). Nothing on the
  machine is changed.

### Acidity, intensity and sweetness on a bean

- **A bean records its acidity, intensity and sweetness**, each on a scale of
  1 to 5 or left unstated (a new migration adds the three columns; nothing is
  rebuilt or lost). The analysis, the starting point, both chats and the SQL
  tool's `v_beans` see them, as one line that says which way the scale runs:
  `taste: acidity 4, sweetness 3 (1 low to 5 high)`.
- **The bean form picks each one on a clickable 5-point scale**, Low to High;
  clicking the chosen step again clears it. The bean's card and the New Set
  dialog's summary of the picked coffee show the ones that are set
  ("acidity 4/5").
- **Only what you filled in about a bean reaches the model.** The analysis and
  the starting point used to write `process: not stated` and `roast level: not
  stated` (and the same for origin, and for a similar Set's bean), and the
  `list_beans` tool sent `null` and `""` for every empty field; a model reads a
  line like that as something known about the coffee. A similar Set's "why it
  is similar" line also called a field "different" when either bean left it
  empty; it now names only what both beans state, and the similar-Set API
  answers `unknown` / `null` for those matches. An empty field is now left out
  everywhere a bean is described to the model.

### The General chat no longer asks for starting points

- **The `starting_point` and `get_starting_point` chat tools are gone.** The
  General chat could ask for three starting recipes for a new bag and tell you
  to accept one, but no screen could take a run the chat had started: the New
  Set dialog takes only the suggestions it asked for itself. The tokens were
  spent and the options sat unused. When you ask about a bag nobody has brewed,
  the General chat now sends you to New Set → **Design it with the agent**,
  which ends in a first recipe you accept. **Ask for suggestions** in the New
  Set dialog, and the Beans page's shortcut, work as before; runs already
  stored stay readable at `GET /api/starting-points/{id}`.

### A profile drafted in a Set's conversation is pushed for that Set

- **The draft card can now push a Set's draft for its Set, and does so by
  default.** A profile the agent drafted in a Set's conversation carries a
  prediction that was to be recorded when you make that proposal active for
  its Set, but the card's only button did it for no Set: the Set never got the
  new version, the prediction was lost, and shots brewed on the new profile
  landed in "needs a Set". The button now reads **Make active and record it as
  v5 of** *the Set*, and records that version with the prediction on it. **Make
  active without recording it on the Set** tries the profile without touching the
  Set. A proposal that belongs to no Set has the one button it always had.
- **A proposal that reached the machine says whether its prediction was recorded**,
  read from the archive rather than from the button pressed: "Recorded as v5 of …"
  only when it recorded a version of the proposal's Set, and "Reached the machine
  without being recorded on …" otherwise. It used to claim the prediction had been recorded
  whatever the push did.

### Design a new Set in chat

- **A design with no profile to fork starts from nothing.** Leaving "Fork
  from" empty used to hand the agent your most-used profile as the base, so
  the new profile inherited whatever of it the agent did not rewrite. Now the
  agent writes the whole profile from scratch, the dialog says so, and the
  draft is compared with the empty baseline on the Profiles page. The agent can
  no longer choose a base of its own either: the profile you pick to fork is
  the only one a design builds on. A document it writes incompletely comes back
  to it field by field to fix.

- **A Set can start with no recipe and be designed in its own conversation.**
  `POST /api/sets/design` takes a bean and a grinder, optionally a profile to
  fork, your usual grind and what you want from the coffee; it creates the Set
  with an empty version 1 and opens that version's chat. The agent is given
  your brief, the profile to fork, how this bean went in your other Sets,
  similar Sets on this grinder and the matching rules, asks what it needs, and
  proposes the whole first recipe — a profile of its own plus grind, dose and
  yield — as one card. Accepting it fills version 1 in place; the profile is a
  draft on the Profiles page to approve and push. A design nobody brewed under
  can be discarded (`DELETE /api/sets/{id}/design`).
- **New Set → Design it with the agent** is the screen for it. It reuses the
  form's bean, name and grinder, reads the form's profile as the one to fork,
  and adds your usual grind and "What do you want from it?"; the grinder is
  required on this path. **Start designing** opens the new Set's conversation
  with what you wanted as the first message. The agent's proposal is a card
  in the conversation and on the Set page showing the profile it drafts (with
  a link to the draft), the grind, dose, yield and ratio, and the reason;
  accepting says version 1 is set and the profile waits on the Profiles page,
  and the card keeps showing the recipe it set.
  A Set being designed carries a **Designing** badge on the Sets list, its page
  and its Chat folder, with **Continue designing**, and its page offers
  **Discard design**; its log reads "being designed — no recipe yet". The
  Chat page lists the tools a design conversation actually has.
- **`get_profile`**, a new read in every conversation, returns a profile
  version's whole document.
- A new migration adds four columns and changes nothing that exists: no reset.

### Update Claude Code from the settings page

- **The Claude Code panel under Settings → LLM can install a newer CLI**
  without rebuilding the image, as cvclanker's does: the `stable` or `latest`
  channel, or an exact version. The release comes from npm, is checked against
  the registry's sha512 and run once before the app switches to it, and lives
  in the data directory (`claude-code/`, about 230 MB), so it survives
  restarts. **Use the image's version** goes back. A newer image that carries
  the same release or a later one takes over again at boot; a downgrade made
  on purpose is kept until the image changes. A custom `claudeCodeBin` still
  wins over both. Nothing updates on its own.

### The open shot row is the shot page's judgement and curves

- **A row in the shots list opens onto the shot page's own two boxes**, laid
  out as on the page: the judgement across the top, the same form as on the
  page (doses, grind, decision, the notes in its right-hand column, saved with
  **Save judgement**), and the Curves box on a row of its own below it with
  its series toggles and downloads. The quick judgement that saved on every
  click and the machine's notes card are gone from the row; the notes card is
  still on the shot page.
- **The judgement's two columns follow the width of the card**, not of the
  window, so the form splits wherever it has room and stacks where it does
  not.

### A shot's page opens on your judgement

- **The judgement comes first, the curves below it.** A shot's page now shows
  your judgement straight under the shot's facts, and the curves on a row of
  their own after it, at the page's full width. The execution score, the Set
  and the analysis follow in the order they did before.
- **The judgement is two columns**: rating, balance, decision, flavour notes,
  doses and grind on the left, the notes on the right at the full height of
  the card. Only a phone-sized window keeps the single column.

### Several bags at once, and one rule that files a shot

- **Any number of Sets collect shots.** A Set used to be "the active one" or
  not, one at a time, which assumed one hopper: with two grinders there are two
  coffees loaded and no answer to "which Set is the current one". Each Set now
  says for itself whether new shots on its profile are filed under it, and as
  many as you like can. The Sets list and a Set's page carry the switch and an
  **automatch** badge where the **active** badge used to be.
- **One rule for every shot.** A shot — pulled, imported, or waiting from
  before — is filed under the one Set that brews its profile: exactly one Set
  set to collect, whose current version names that profile. Two such Sets, or
  none, and it waits in the inbox saying which it was. The **Match by profile**
  button on the Shots page and on a shot's page runs the rule over the shots
  already waiting. A shot you filed by hand is never moved.
- **The Automatch new shots tickbox is gone**, from both pages and from
  Settings → Machine access, and so is the stored setting behind it. Every new
  shot goes through the rule; the switch on the Set is what says where. A Set
  that names no profile still collects nothing on its own — it is offered in
  the pickers, to choose by hand.
- **A Set's `status` is now an `archived` boolean.** It only ever held "active"
  or "archived", and its "active" was not the other flag's "active". Your
  database is upgraded in place on its next start: every Set you have not
  archived is set to collect shots, so what matched before still matches.

### An update never asks you to delete your database

- **Your archive keeps starting across updates.** A database refused to start
  whenever an update had touched any migration file, even when only a comment
  changed, and the only way past it was deleting the database. The check now
  looks only at the SQL a migration runs, so documentation edits can no longer
  stop an archive from starting, and a test now fails any update that would
  change what a shipped migration does. Your current database is upgraded in
  place on its next start (only its migration records are rewritten); nothing
  else about it changes.

### The Claude Code provider works out of the box, and Validate tests what you typed

- **The image carries the Claude Code CLI.** `claude_code` is the default
  provider, and it runs the `claude` command — which the image did not have, so
  on a fresh install every Validate and every analysis answered "the Claude
  Code CLI (claude) was not found on PATH" however the token was set. The CLI's
  native binary is now in the image (pinned, 2.1.267; about 220 MB), on the
  path the `claudeCodeBin` setting already defaults to. Rebuild the image to
  pick it up.
- **Validate credentials no longer needs a save first.** It used to test what
  was stored, so a token pasted into Settings → LLM and validated straight away
  came back "No Claude Code OAuth token is configured", and switching the
  provider picker validated the provider you were leaving. It now tries what
  the form holds and stores nothing.
- **Validate proves a Claude Code token works, not only that one is set.** It
  used to ask `claude auth status`, which says "logged in" for any string at
  all, so a mistyped or revoked token validated green and the first analysis
  failed. It now also makes one tiny real call (a one-word answer from haiku,
  a few dozen tokens of your subscription, only when you press the button);
  a token Anthropic refuses says so and how to mint a new one. The status
  panel's badge still uses the free check, so opening Settings costs nothing. A saved API key is not sent along to a
  different provider or address you picked but have not saved: type that
  provider's key to validate it.

### The shot list uses the whole window, and every column can be sized

- **The list is as wide as your window** instead of the reading column the rest
  of the application is laid out in, up to a cap and with a margin either side.
  The archive is a table with eleven columns to offer, and the ones worth
  having — the profile, the curve, your notes — were the ones there was no
  room for. The compare drawer widened with it, so its chart lines up with the
  table above it.
- **Profile is on the row from the first visit**, and can be resized like every
  other column. It and Notes used to take whatever the row had left over, which
  also meant their edges could not be dragged; they are ordinary columns now,
  with a width you set and a browser that remembers it. If you had never
  touched the column chooser — or had ticked your way back to exactly what it
  gave you — Profile simply appears; any other choice you made is kept as you
  made it.
- **Every column starts at the width that fits it** — the sparkline, five stars,
  the three decision words, or the heading and its sort arrow — rather than at
  a share of the row. Widths you had already dragged are untouched.
- **The table is as wide as its columns, not the window.** The box, its border
  and the count under it end at the last column, and the table is centred in
  the page, so a few narrow columns no longer sit at the left of a wide band of
  empty rows; turning a column on or
  dragging an edge wider grows the table, up to the page, and past that it
  scrolls sideways.

### An agent's change to a Set is a proposal you accept, and it needs a prediction

- **The chat can no longer change a Set on its own.** `propose_set_version`
  used to record the next version the moment the model asked for one: your next
  shot was filed under a recipe you had never agreed to. It now writes down one
  change, with what it expects that change to do, and leaves it waiting for you.
  The Set stays where it is until you press **Accept**.
- **Accept and Decline are on the card, wherever you read it** — in the
  conversation it was argued in, and above the experiment log on the Set page.
  The card says what would move, why, and what is predicted, *compared to v4*.
  Accepting records the change as the Set's next version with that prediction on
  it, and sends nothing to the machine. Declining creates nothing, and the note
  you leave is what the next conversation is told about what you did not want.
  Accepting is refused, in words, if the Set has moved on since the change was
  proposed, or if the current version's own prediction has not been graded yet.
- **A proposal without a prediction is refused**, and so is one that moves two
  things at once unless the agent says why they cannot be separated — and then
  it has to tell you that the prediction cannot say which of them did anything.
  While the current version's prediction is ungraded the agent cannot propose
  anything new: it grades that one with you, or asks for another shot on the
  same recipe, which needs no version. Only one proposal waits per Set.
- **A profile drafted inside a Set's conversation carries a prediction too**,
  because the temperature and the pressure curve are as much of the recipe as
  the grind is. The draft still lands on the Profiles page as an ordinary draft,
  with the same schema, safety-policy and clamp checks and the same approve and
  push it always had; the prediction is recorded on the Set when you push that
  draft for that Set, and nowhere else. Every card that shows a draft says what
  it predicts and where that prediction will land.
- **Accepting and declining are yours alone.** There is no tool, in the chat or
  over MCP, that reaches either, and there is no new kind of permission: the
  machine is still written only through this app's own routes, by a person.
- A version that came from an accepted proposal links back, from the experiment
  log, to the conversation it was argued in.

### A chat is about one experiment, and can see only that

- **A conversation in a Set's folder is about one version of that Set** — the
  change being argued — and the version is fixed when the conversation starts.
  Press **New** in a folder and you get a fresh session on the Set's current
  version; it stays on that version afterwards, so the folder reads as a history
  of what was argued rather than as a pile of rooms all claiming to be about
  today's recipe. Rows are labelled `v6`, and a conversation about a version a
  later roll back stepped over is muted and says *dead end*.
- **It can see that Set and nothing else.** A Set conversation has tools for its
  own versions, predictions, outcomes and shots — including a new one that lists
  this Set's shots with the six measures, the rating, the balance, the flavour
  notes and the label, filtered by version or by label — plus the knowledge
  tiers, and the three things it can propose: the next version of this Set, an
  insight about it, and a profile draft. It has no archive-wide SQL,
  no list of your other coffees and no way to read a shot filed elsewhere: those
  tools are not offered to it, and a shot of another Set is refused in the same
  words whether or not it exists, so a refusal cannot be used to find out what
  else you brew.
- **General is the other half**: the whole archive, read-only. It queries, it
  compares across Sets, it works out a starting point for a bag with no Set yet
  and it drafts profiles — and it cannot change a Set, because a change to a Set
  is an argument that belongs in that Set's own room. It says which folder to
  open instead.
- **The agent is handed the experiment before it says a word**: the Set and the
  recipe, every version with what changed, what was predicted, against which
  version and how it turned out, the track record, the spread, the evidence
  table this version's prediction is graded on, both compared versions' shots
  one line each with the discards marked, the Keep shots that are the target,
  and the insights you have confirmed. Long histories are summarised from the
  oldest end, never dropping this version or the one it is compared against.
- **The agent's instructions changed from "barista with tools" to "rigorous
  experimenter's assistant"**: grade the open prediction first, claim by claim,
  against every counted shot of both versions and against the spread; say
  *inconclusive* rather than stretch one shot; call a reason that was not in the
  prediction a new hypothesis, to be tested by the next prediction; compare
  Improve shots with the Keep shots; propose one change, stated as a direction
  and a rough size on a measure the archive records, against a named version;
  when results drift with no recipe change, say the cause is probably outside
  the record and ask; never decide the Set is finished.
- **Discuss in chat opens or continues one conversation.** On a Set it is the
  current version's; on a shot it is the version that shot was pulled under;
  every entry in the experiment log has a **Chat** link to the same room. Press
  one twice and you land where you were, with the question still typed. A `?set=`
  link on its own still creates nothing until you send something.
- Beside the composer, the page lists exactly what the agent can do in *this*
  conversation, from the server rather than from a list in the browser.

### The spread, and the evidence behind a prediction

- **A Set now says how much its shots vary when nothing in the recipe
  changed** — "Shot time ±1.8 s · from 9 repeat shots of 3 recipes", above the experiment
  log. Shots brewed with the same profile, grind, dose and target yield are
  repeats of each other, wherever they were filed, so a roll back's shots count
  as repeats of the recipe it copied. Each group is measured against its own
  average and those distances are pooled across the Set, which is what lets
  many versions of two or three shots each add up to a usable figure. Six
  measures: shot time, time to first drip, yield, peak pressure, average brew
  flow and your rating, each used only where the archive already holds it — a
  measure nothing records is left off the block. The yield follows the order the
  rest of the app already uses: the one you typed, then the scale, then the
  device's own index. It is arithmetic the app does, the same way every time; no
  model is involved.
- **Until there is enough to trust, it says so and names a floor.** Below three
  degrees of freedom the line reads "not measured yet · differences under 2 s
  are not counted". The floors are 2 s for the shot time, 1 s for the first
  drip, 1 g for the yield, 0.3 bar for the peak pressure, 0.2 ml/s for the brew
  flow and half a star for the rating — first numbers, to be tuned with use.
- **Every version that predicted something carries its evidence.** An Evidence
  disclosure in the log lays all of that version's counted shots beside all of
  the compared version's, measure by measure, with the mean and the count on
  each side, the difference with its sign, and whether it is beyond the spread
  or inside it — in words, with what it was held against, and a sentence saying
  what that yardstick is. Differences and yardsticks are written a decimal finer
  than the means, so a row never reads "+2.0 s, beyond 2.0 s" when the
  arithmetic found "+2.04 s, beyond 2.00 s". The balance and the
  Keep / Improve / unlabelled counts for both sides sit under it as plain
  facts. It opens by itself on a prediction nobody has graded yet that has
  shots to grade it with. Quarantined, incomplete and Discard shots are left
  out of all of it; unlabelled shots count.
- None of this appears on the shot page or the quick judgement panel: the
  prediction stays hidden there until the shot is labelled.

### A Set version has no temperature of its own

- **The brew temperature comes from the profile, everywhere.** A Set version
  used to carry a temperature somebody typed, and the machine never read it:
  it heats to what the profile says. "94 °C" on a Set could be a change that
  never happened — and, with version predictions, a prediction graded against
  shots brewed exactly as before. The field is gone from both recipe forms,
  which now show what the picked profile brews at, read-only, with a line on
  how to change it: edit the profile on the machine and record the new version,
  or draft one on the Profiles page.
- **The experiment log shows a temperature change as part of a profile
  change** — "Temperature 93 → 94 °C" beside the new profile, marked as coming
  from it. A version can no longer differ from its parent in temperature alone.
- **A temperature suggestion** from an analysis is now treated like one about
  the pressure or the flow: the card offers no "Record it as a new version"
  button and explains that this is a change to the brew profile, pointing at
  drafting one from the analysis. The model may still advise a degree either
  way; it is the profile that carries it. Which variables a card offers to
  record now comes from `GET /api/vocab` rather than from a list in the front
  end, so it cannot drift from what the server will accept.
- **The chat's propose-a-version tool** no longer takes a temperature, and its
  description sends a temperature change to the profile-draft tool. The views
  it reads SQL over carry `profile_temperature_c` — the profile's own number —
  in place of the Set's.
- **Taking a starting point stages a draft when it has to.** An option still
  suggests a temperature, and if it points at a profile you already have that
  brews at a different one, taking it proposes a draft of that profile at the
  suggested temperature and the new Set's first version points at the draft.
  The card says so beforehand. Nothing is sent to the machine: you approve and
  push it on the Profiles page, as with any other draft.

### The chat's conversations, in a folder per Set

- **A folder per Set** replaces the chronological list: General first, then
  every Set you have not archived — including ones with no conversation yet —
  and a last folder for conversations about Sets you have archived. A folder
  opens itself when it holds the conversation you are reading, or when a
  **Discuss in chat** link names its Set.
- **New inside a folder** creates the conversation there and then, already
  scoped to that Set. The "Scope a new conversation" select under the list is
  gone: it was the thing that scoped a conversation, and it overflowed the
  card it lived in.
- With nothing selected, the composer's card says where a first question will
  go — "A new conversation in <Set>" or "A new general conversation".

### The Set page is an experiment log

- **A version can change the profile.** "Change something" now offers a
  Profile field, first and preselected to the one the Set is brewing with, so
  switching profiles — or editing one on the machine — is something you can
  record. Without it those shots landed in "needs a Set", because a shot joins
  its Set by the profile it was pulled with. Picking one carries its target
  yield across, never over a number you typed, and shows what it brews at. It
  sends nothing to the machine: putting a profile there is still the Profiles
  page.

- **A version prediction.** A Set version can say what you expect it to do
  differently and which earlier version that is against — the parent by
  default, or nothing at all if you would rather grade it on the numbers the
  version itself states. It is optional, and it can only be written **before
  the version's first shot** and before its prediction has been graded: one
  typed after the cup was tasted grades itself, so the app refuses it with a
  message saying why.
- **An outcome.** Once a version has a prediction and a shot you labelled Keep
  or Improve, you grade it: held, partly held, failed or inconclusive, with a
  line saying why. A grade can be changed or taken back at any time.
- **The track record.** Above the log: "6 of 10 predictions held", with how
  many are still open and how many versions predicted nothing.
- **Roll back to an earlier version.** One click, with a confirm step,
  appends a new version whose recipe is the old one's. Versions that are no
  longer on the line you are brewing — read backwards from the current version,
  through what each roll back restored — are marked dead ends and muted, still
  fully readable. When the current version has shots you want to improve on and
  none you kept, the page offers the way back to the last version you did keep
  shots from. **Nothing is sent to the machine**, even when the restored version
  names a different profile.
- **Each version shows how its shots were labelled** — "2 Keep · 1 Improve" —
  beside the shot count, and that count links into the shots list narrowed to
  **that version** rather than to the whole Set. The filter panel says which
  version is on ("Guji on the Niche v5") and removes it in one click; changing
  or clearing the Set drops it.
- **The prediction is hidden while you judge a shot.** On the shot page and in
  the shots list's panel, a shot whose version predicted something says so but
  does not show the words until the shot carries a decision. "Show prediction"
  reveals it for that view only; nothing is remembered.
- **The chat can read the ledger.** `v_set_versions` carries the prediction,
  what it is compared to, what a version restores and the outcome.

### A judgement for every shot, on the flavour wheel

- **Quick judgement under a shot row.** The panel that opens under a row is no
  longer the full form with a Save button. It holds what you can fill in for
  every shot in a few clicks: rating, balance, aroma notes, taste notes and a
  line of notes. Every click saves; the notes save when you leave the field or
  press Ctrl/Cmd+Enter. Doses and grind stay on the shot page, whose form
  writes the same verdict.
- **The flavour wheel.** Taste and aroma are recorded as notes on the SCA/WCR
  Coffee Taster's Flavor Wheel, all three tiers, from "Fruity" to
  "Blackberry". It replaces the old taste chips (sour side, dialled in, bitter
  side, strength). The balance — sour, balanced, bitter, the GaggiMate's own
  three words — stays a control of its own, and it is still what goes to the
  machine's notes card; the wheel's notes stay here.
- **Taste wheel page** under Brew setup (`g w`): the wheel, drawn, where you
  pick which notes the shot panel offers for taste and for aroma. A fresh
  archive starts with ten or so of each. The chips under a shot show your
  picks plus anything already recorded on that shot.
- **Decision on the row.** The shots table's Analyse column is replaced by a
  Decision column: Keep, Improve or Discard, one click each, a second click
  to clear. "Adjust" is now "Improve". Start an analysis from the shot page;
  the Flags column still shows where it got to. If you had Analyse among your
  columns, Decision takes its place.
- **A narrower Set column.** The Set column has a drag handle like the others
  and starts narrower; a long Set name truncates, with the whole name on
  hover.
- **The analyzer reads the wheel.** An analysis sees your notes with their
  path on the wheel, and the taste rules on the Knowledge page are keyed on
  wheel notes and the balance. The rules about mouthfeel, strength and finish
  (astringent, watery, flat, too intense) have no note on the wheel, so no
  analysis selects them for now; their text is still there.

### Settings and the sidebar, regrouped

The sidebar has five rows instead of nine: Shots, Chat, **Brew setup** (Sets,
Beans, Hardware), **Machine** (Profiles, Sync, Device) and **Settings**. A
group opens when you click it, and on its own while you are on one of its
pages; one you open by hand stays open next time. Every `g` chord still goes
where it did, and the device page has a row of its own under Machine.

Settings is a group of pages rather than one long page, in the order you are
likely to need them: **Machine access** and **LLM** first, since nothing works
until both are filled in, then Authentication, Knowledge, Prompts, Profile
safety, System and Import. On each page the settings sit in cards under
subheadings, all closed until you open one; a save that fails validation opens
the card with the problem. The page that used to be called Machine is Machine
access, so it is not confused with the sidebar's Machine group, and the old
General page's analysis and chat budgets are cards on the LLM page. Knowledge
keeps its own address, so every citation link into it still works.

### The Beans page after using it

- **No variety.** The field is gone from the bean, the form, the card, the API,
  `v_beans` and both prompts: it was blank on most beans and nothing reasoned
  from it.
- **Description.** "What the bag claims it tastes of" is now `description`, a
  free-form description of the coffee in your own words (what the bag or the
  roaster says, tasting notes, anything worth knowing), in a three-row box, up
  to 2000 characters. The API field and the `v_beans` column are `description`
  (formerly `tasting_notes_bag`), and the analysis and starting-point prompts
  receive it as `description`.
- **Decaf** sits in the grid with a label like the other fields.
- **Roaster and origin suggest what you have already recorded**, archived beans
  included: type to filter, pick with the mouse or with the arrows and Enter.
  Anything else you type is kept as typed.
- **A bean can be deleted**, after an inline confirmation: `DELETE
  /api/beans/{id}`. A bean any Set uses cannot (409, with the number of Sets);
  archive it instead. Starting-point runs about a deleted bean go with it.

### MCP has no network endpoint

The Streamable HTTP endpoint at `/mcp` is gone, and so is its switch. MCP is the
chat's own database tool: the `claude_code` provider spawns `gaggiclanker mcp`
and talks to it over stdio, and nothing listens on the network for it. `/mcp` now
answers like any other unknown path. The README no longer documents wiring the
command into Claude Desktop or other outside agents; the command itself is
unchanged for the provider that uses it.

The chat's tools are handed nothing that can reach the machine: creating a draft
goes through an object built without the machine connection, which the
starting-point wizard uses too, and a test walks what a tool is given to prove
no device client or connection is reachable. `draft_profile` now works when the
chat runs on the `claude_code` provider, where it used to answer that it needed
the running application.

Removed setting: `mcpEnabled` (and its `GAGGICLANKER_MCP_ENABLED` variable). A
stored value is deleted at upgrade (migration `0019`), and a boot with the
variable still set names it in the `setting_env_ignored` line described below.

### Clone and start, and settings live only in the database

**Starting is `git clone`, then `docker compose up -d --build`**, then entering
the machine's address under Settings → Machine access. There is no file to copy, rename
or edit first: `.env.example` and the `.env` that briefly replaced it are both
gone, and the repository ships no configuration file at all.

**Breaking: the default port is now 8042**, not 8000 — it was the one number in
this project likely to collide with something else on a home server. It is still
the only thing given at spawn, because the process has to bind before it can read
the database, and it is remembered nowhere: pass it again each time, from your
shell or from a `.env` Compose substitutes from. To keep 8000, start with
`PORT=8000 docker compose up -d --build`; with the bridge arrangement,
`HOST_PORT=8000 docker compose up -d` publishes on 8000 and leaves the container
on 8042. The image, its healthcheck, compose's healthcheck and the Vite dev proxy
all moved together.

**Breaking: no runtime setting is read from the environment any more.** A
setting is what the Settings page saved, or the shipped default — those are the
only two possibilities, and `GET /api/settings` reports `source` as `database` or
`default` accordingly (`environment` is gone from the field and from the badge on
the Settings page). Every variable that used to configure one — `GAGGIMATE_HOST`,
`GAGGIMATE_PROTOCOL`, `GAGGIMATE_TIMEOUT_S`, `GAGGICLANKER_DEVICE_SYNC_ENABLED`,
the `GAGGICLANKER_DEVICE_CLEANUP_*`, `GAGGICLANKER_NOTES_WRITEBACK_FIELDS`, the
`GAGGICLANKER_PROFILE_POLICY_*`, `GAGGICLANKER_LLM_*`, `GAGGICLANKER_MODEL*`,
`GAGGICLANKER_ANALYSIS_CHUNK_TOKEN_BUDGET`, `GAGGICLANKER_CHAT_*`,
`CLAUDE_CODE_BIN` and `CLAUDE_CODE_EFFORT` — now does nothing. Two
configuration surfaces that could disagree silently, on a box whose whole
configuration fits on one page, was one too many.

Compose no longer passes a machine address through. The only variable it still
sets is `LOG_LEVEL`; the image sets `DATA_DIR`, `HOST`, `PORT` and `WEB_DIST`,
and `LOG_JSON` and `CORS_ORIGINS` fall back to their defaults. Those seven are
the whole of what the process reads from the environment, and the README's
Configuration section documents each one.

Nothing parses a `.env` any more — not the settings, not the bootstrap values,
not the credential check — so a file left next to the compose file is logged once
as `dotenv_file_ignored` with its full path and read by nothing.

`compose.yml`'s `environment:` block is not configuration either: it forwards
exactly the variable names a boot refuses (the sign-in and LLM credentials, and
`GAGGICLANKER_DEVICE_WRITES_ENABLED`) and nothing else, each as `${NAME:-}`.
Compose substitutes those from a `.env` in the project directory as well as from
your shell, so a box upgrading from the `.env.example` era with credentials still
in that file stops at boot with the names in the log, rather than coming up with
the sign-in that file used to configure silently off — while a stale `DATA_DIR`
or `PORT` in the same file reaches nothing. An unset or empty variable
substitutes to empty, which counts as unset, so a clean box boots.

**Upgrade note: store your configuration in the database before you pull.**
While the old version is still running, move anything you had set — in a `.env`,
in `compose.yml`'s `environment:`, or in the shell — into the database, and then
remove the variable. A boot that still finds one set logs `setting_env_ignored`
once with the names (never the values) and starts on the database's values.

Note that simply typing the value into the old Settings page and pressing Save
does **not** store it: the form sends only the fields whose value differs from
the one displayed, and a value coming from the environment is already displayed,
so Save sends an empty change. Either change the field to something else, save,
change it back and save again — or store it directly, which is one request:

```bash
curl -X PATCH http://localhost:8000/api/settings \
  -H 'content-type: application/json' \
  -d '{"gaggimateHost": "192.168.1.50", "deviceCleanupMode": "keep_newest"}'
```

Add `-H "Authorization: Bearer <token>"` if sign-in is on, and check what stuck
with `curl -s localhost:8000/api/settings`: every key you moved should read
`"source": "database"`. Port 8000 because that is the old version's default —
see the port change below.

Two are refused rather than ignored, and the container exits naming them: any
variable that used to carry a credential (see *Credentials leave the environment*
below) and `GAGGICLANKER_DEVICE_WRITES_ENABLED`, which used to open the only path
from this box to the machine. Turn writes on with the **Writes** switch in the top bar instead.
Silently ignoring either would leave a box less protected than its owner
believes. An empty value counts as unset in both cases, so an old compose file
passing `${GAGGICLANKER_DEVICE_WRITES_ENABLED:-}` through still starts.

If `git pull` stops on your own `.env`, move it aside — nothing reads it now.

### Machine settings apply live

Changing the machine's host, protocol, timeout or **Device sync enabled** under
Settings → Machine access now takes effect on save: the connection to the old machine is
closed and the new one opened, with no restart, and the header pill and the Sync
page follow straight away. A save that leaves the effective values as they were
does nothing to the connection. While a profile push or rollback, a cleanup run,
a notes send or a sync is using the machine, a change that would move the
connection is refused with `409` naming what is running, and nothing in that save
is stored; other settings save as usual. A sync asked for while such a save is in
progress waits for it. A sync cut short — by a connection change or by stopping
the app — is now recorded as an error saying it was stopped; it used to be filed
as `ok` with nothing archived.

### Credentials leave the environment

**Breaking: move sign-in and provider keys into Settings before you upgrade.**
The sign-in user and password and every LLM provider's API key or token are now
configured only in the Settings page and kept only in the database. A boot that
finds one of the variables that used to carry them, or that the provider SDKs
would read a credential from, set non-empty — `AUTH_USER`, `AUTH_PASSWORD`,
`AUTH_PASSWORD_HASH`, `AUTH_TOKEN_TTL_S`, `AUTH_JWT_SECRET`,
`GAGGICLANKER_AUTH_PASSWORD`, `GAGGICLANKER_AUTH_JWT_SECRET`,
`GAGGICLANKER_LLM_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_CUSTOM_HEADERS`, `OPENAI_API_KEY`,
`OPENAI_ADMIN_KEY`, `OPENAI_CUSTOM_HEADERS` or `OPENROUTER_API_KEY`, in any
letter case, in the process environment — refuses to start: it logs
`auth_env_refused` with the variable names (never a value) and exits non-zero.
So does an `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` (either case) carrying a
user or password; a proxy without one is still used.
Ignoring them instead would have switched authentication off on an install that
had configured it only there. Empty values count as unset, so a compose file that
still passes `${AUTH_USER:-}` through starts normally.

To upgrade an install that set any of these: while the old version is still
running, set the password and then the username under **Settings →
Authentication**, and paste each key or token under **Settings → LLM**; then
remove the variables and upgrade. The session signing key is always generated
into the database now (backups carry it), so sessions survive restarts as before;
the one case that changes is two processes sharing sessions through a common
`AUTH_JWT_SECRET`, which is no longer possible.

A lost password is recovered by deleting the stored `authPasswordHash` row, which
turns sign-in off on the next request with no restart, and then setting a new
password under Settings → Authentication; the README has the one-liner. The
`claude_code` provider's CLI now gets its token only from the setting, never from
the app's own environment, and no proxy with credentials in it. The OpenAI and
Anthropic clients are handed the stored key and an explicit base URL, ignore the
SDKs' environment headers, organisation and project variables, profile files and
`.netrc`, and do not follow redirects. An `openai_compatible` provider with no
base URL stored now reports "set llmBaseUrl" instead of calling OpenAI's
default address.

### Every write to the machine is explicit

**Profiles may be pushed by the app; everything else written to or deleted from
the machine happens only from the new Sync page, by a person.** Three things that
used to happen on their own no longer can.

**A Sync page.** A new **Sync** entry in the sidebar (`g y`) holds every exchange
with the machine that you start: **Sync with the machine**, **Send notes to the
machine**, **Clean up the machine's storage** and **Recent writes**. Each write
action says what is in the way when it cannot start — no machine configured,
device writes off, or the machine not connected. The Device page keeps what the
machine is, its versions and its connection, and links to the Sync page; its old
storage, notes, sync and writes anchors redirect there.

**Saving a judgement never contacts the machine.** Notes go to the machine's
notes cards only when you tick judgements on the Sync page and confirm; nothing is
ticked for you. The rules about what may be sent are unchanged: a card edited on
the machine more recently is left alone, and a verdict that came from the machine
and was never edited is never sent back. The **Sync notes to machine** button on
a shot is gone.

**A cleanup never runs by itself, and runs exactly what you confirmed.** The Sync
page shows the plan with the reason each shot is in it and, folded, the shots
kept and why. Confirming names the count and says it cannot be undone on the
machine (the archive keeps every shot). If the plan changed between the preview
and the confirmation — a sync landed, a setting moved — nothing is deleted and you
are asked to look again.

**MCP is read-only by design.** MCP clients get exactly the in-app chat's tools:
read, and propose something a person confirms. No tool can write to the machine,
and no setting adds one.

Removed settings: `mcpDeviceWrites`, `deviceCleanupAuto` and
`notesWritebackEnabled` (and their `GAGGICLANKER_MCP_DEVICE_WRITES`,
`GAGGICLANKER_DEVICE_CLEANUP_AUTO` and `GAGGICLANKER_NOTES_WRITEBACK_ENABLED`
variables). The **Writes** switch in the top bar is the one switch in front of every write;
`deviceCleanupMode` and its two numbers now shape the plan the Sync page proposes,
and `notesWritebackFields` picks what a send writes. Stored values for the removed
settings are deleted at upgrade (migration `0018`), and a boot with one of the
variables still set logs `setting_removed_env_ignored` naming it.

API: `POST /api/device/cleanup/run` requires `{"shot_ids": [...]}`, the planned
shot ids you confirmed — at least one, an empty list is a 400 — and answers 409
when the plan has changed; each planned shot carries a `reason`, and the plan's
policy loses `auto`. `POST /api/device/notes/push` requires `{"shot_ids": [...]}`,
the judgements you ticked — at least one; there is no form that sends everything
pending — and answers 409 when a selected one is no longer pending.
`GET /api/device/notes/pending` returns `items` — each with the shot's device id,
time, profile and verdict — in place of `shot_ids`, and loses `enabled`. Both
write routes answer 403 when device writes are off, and record the refusal.
`POST /api/shots/{id}/notes-writeback` is removed.

### The shots table

**A row opens in place.** Clicking a row no longer opens the shot page: it
unfolds a panel under the row with the shot's curve, the full judgement form and
the machine's device notes, so a shot can be judged without leaving the list.
Clicking the row again, or pressing Escape, closes it; opening another row closes
the first. Clicking the curve or the notes, or **Open shot page**, goes to the
shot page. A quarantined shot shows its reason instead of a curve.

**An Analyse column, in place of Flags.** Each row says whether it has been
analysed and offers the action: **Analyse**, **Analysing…** while it runs,
**Analysed** linking to the analysis on the shot page, or **Retry** with the
failure's reason on hover. A quarantined shot cannot be analysed, as on the shot
page. Flags is still in the column chooser. If you had stored exactly the old
default columns, you get the new default; any other choice of columns is kept.

**Columns can be resized.** Drag the edge of a heading, or focus it and use the
arrow keys; double-click the edge to reset that column, or use **Reset widths**
in the column chooser. Widths are remembered in the browser.

**Compact time, centred columns, Set first.** The time shows day, month and time
for this year's shots and the date with its year for older ones, with the full
timestamp on hover, in a narrower column. Headings and values are centred, and
the Set is the first column. Columns no longer drift out of line when a Set name
is long.

The shots API's list rows gain `analysis_error`: the newest analysis's error when
it failed, otherwise null.

### Filing a shot from the list

**"needs a Set" is a button.** In the shots list the dashed badge opens a small
menu anchored to it with the first three Sets the Set list returns (the active
one first, then the newest), each at its latest version with its bean, grinder
and profile on one line. Choosing one files the shot under that version and the
row's badge becomes the Set's. With no Sets the menu links to the Sets page;
with more than three, **Another Set…** opens the shot page scrolled to its Set
panel (`/shots/<id>#set`). A failed assignment says why and leaves the menu
open.

**An assigned badge's link works in the list.** It was painted under the row's
own link, so clicking it opened the shot instead of the Set.

### Starting a Set is one form

**New Set is a single dialog** with every manual option on one screen: the bean
(with its facts line), a name defaulting to the bag's, the grinder, the profile
version, grind, dose, target yield, temperature and an optional intent, and one
**Start the Set** button that waits for a bean. The five-step wizard (Suggest,
Bean, Hardware, Profile, Recipe) is gone. The Beans page shortcut still opens it
with the coffee picked, and a refusal from the server still keeps what you
typed.

**The AI starting point is folded under the form** as **Suggest a starting
point instead**. It uses the bean and grinder already picked; asking, the three
options and taking one behave as before, including landing on the proposal
when the option authored a profile.

**Picking a profile fills the recipe.** Target yield and temperature come from
the profile version when it states them: the temperature is the profile's own
(0 means not set), and the yield is its largest volumetric stop — `pumped`
targets are water, not coffee, and utility profiles stop on nothing, so they
offer no yield. A field is filled only when it is empty or still holds what the
previous profile filled; anything typed stays, and a hint says which numbers
came from which profile. `GET /api/profile-versions` rows carry the two numbers
as `temperature_c` and `target_yield_g`, and the style detector's allongé check
reads the yield through the same helper.

### A bean has no altitude

Almost no bag prints the growing altitude, so the field was empty on most beans,
and the only thing it fed was one rule nudging dense high-grown coffee a degree
or two hotter and a step finer — the move the roast level and the taste of the
cup already lead to.

- **Removed**: the Altitude field on the Beans form and the metres on the bean
  card, `altitude_m` on the bean API and the chat's `list_beans`, the altitude
  line in the analysis and starting-point prompts, and the `altitude:high`
  signal.
- **One rule leaves the seeded knowledge tier**: `temperature_by_roast`/
  `high_altitude`. An archive that already holds it keeps the row — seeding
  never deletes a row somebody may have edited — but nothing emits the signal it
  matches on, so it is never selected. The seeded reference prose is unchanged.

Migration `0017` drops the column. `v_beans` names it, and SQLite re-parses every
view when a table is altered, so the view is dropped and re-created without the
column; the chat's SQL views answer everything else as before.

### One machine

The archive was built to hold several, and the generality cost correctness
rather than surface. Identity was the configured host, so a display board that
changed address became a *second* machine: its shots, profiles and Sets split
from the old ones, the active Set stopped auto-assigning, and nothing merged
them back. An import done before the machine was configured — the natural order
for a new install — landed on a synthetic `import:default` machine, and the same
shot could then exist twice, because the unique key included the machine.

`machines` is now a one-row table describing whatever host is configured. The
host is a setting, not an identity: pointing the container at a new address
updates that row and everything stays attached. A shot is unique by the id the
device gave it, one Set is active overall, and no route, form, chat tool or MCP
schema takes a machine id. Grinders stay plural — a kitchen really does have
several, and a grind number only means something on the grinder it was set on.

**Upgrading merges what you have, and it is worth knowing the rules.** Migration
`0016` keeps the machine with a real host, and the most recently seen of several
real hosts; the importer's `import:default` placeholder always loses. Everything
that pointed at the others is re-pointed at the survivor. Shots are then
de-duplicated by their device id: the copy whose bytes came off the machine
wins, the newest otherwise, and before the loser goes its samples, its verdict,
its notes card, its analyses and its Set assignment move across wherever the
survivor has none. The profile mirror de-duplicates the same way, keeping the
live mapping over a tombstone. If two Sets were active, the survivor machine's
stays active and the others are simply no longer *the* one — nothing is
archived. An archive with no machine at all gets the row with an empty host, so
a fresh install and an import-only install both have "the machine" before the
first sync. Take a backup first (`POST /api/backup`); there is no
down-migration.

**`GET /api/machines` is `GET /api/machine`**, answering the row with its shot
counts, and `PATCH /api/machine` still takes only the name and the notes. The
`machine_id` query on `GET /api/shots`, the field on `POST /api/sets`, the form
field on `POST /api/import`, the starting-point request body and the
similar-Sets query are all gone. The Hardware page leads with one **Machine**
card above the grinders, and the New Set wizard has no machine step.

### The sidebar folds

The button at the foot of the rail, or the `[` chord, collapses the sidebar to
an icon rail and back, and the choice is remembered. Folded, every entry keeps
its name — a tooltip for a mouse, the accessible name for everything else — and
the active entry is still marked. The mobile sheet is unchanged.

### Fewer pages, and beans are coffees

Twelve destinations in the sidebar, three of which were not places you decide to
go. They are folded into the page you are already on when you want them.

**The sidebar is eight entries**, in the order they are used: Shots, Chat,
Profiles, Sets, Beans, Hardware, Knowledge, Settings. The `g` chords are
unchanged; `g i`, `g d` and `g r` do nothing now.

- **Import is the drop zone on the Shots page.** It already took the files; it
  now shows what each one did, collapsed to a summary line with a **Show files**
  toggle. `/import` redirects to `/shots`.
- **The device page is behind the header pill**, which is where you are looking
  when you want it. The page, its route and its tests are unchanged, and the
  pill now names its destination for screen readers.
- **A proposal waits inside its profile on the Profiles page.** A draft is the step
  between a profile version and the machine, not a destination: everything that
  creates one starts from a version or ends by linking back to it. `/drafts`
  redirects to `/profiles#staged`, which opens the newest proposal's row, and every
  link that used to point at the queue points at that anchor.

**A new way to bring a profile in:** **Upload profile** in the Profiles header runs a
profile export through the importer, so a file becomes a profile in the list,
switched off, ready to be switched on.

**A bean is a type of coffee, not a bag**, and `beans.roast_date` is gone.
Roaster, origin, process and roast level stay true of every bag you
ever buy of that coffee; the date was true of one of them, so re-buying either
aged the old row silently or forced a duplicate bean.

- **Removed**: the field on the Beans form, the freshness pill on the Beans and
  Sets pages, `bean_roast_date` on the Set list row, `roast_date` on the bean
  API, the days-off-roast line in the analysis prompt, and the rest note in the
  starting point. The bean list is alphabetical now that there is no freshest to
  put first.
- **Ten rules leave the seeded knowledge tier**, and with them two whole
  categories: the five `freshness_windows` and the five `rest_times`. Both are
  advice about how long a bag has been open, which is unactionable without a
  date and would only be prompt weight. An archive that already holds those
  rows keeps them — seeding never deletes a row somebody may have edited — but
  a retired category is never selected and sorts last in the rule list. The
  seeded prose on bean freshness and storage stays; it is retrieved by a
  question, not injected.
- **Bag ageing is not tracked at all for now.** It is a real thing about coffee
  and it may come back as its own row with its own dates.

Migration `0015` drops the column, and with it the two curated views that name
it, re-creating them underneath — SQLite re-parses every view when a table is
altered, so the drop fails outright while they stand. The chat's SQL views lose
`roast_date` and answer everything else as before.

### The archive is pulled into, not pushed at

The prototype mirrored the machine continuously: a pass every fifteen minutes,
a pass on every reconnect, a pass on every `evt:history-shot-saved`, a live shot
view redrawing at 2 Hz, and a fake device that brewed on a timer to feed it. In
use none of it earned its place — the GaggiMate's own web UI already draws the
shot that is happening now, and duplicating it here was a second, worse copy
that spent the machine's two HTTP slots on it. What a person wants from an
archive is: press a button and have the new shots, drop a file and have it
archived, then say what the cup was like without leaving the list.

**Getting data is a request.**

- `POST /api/sync/run` is the only trigger for shots, profiles and notes. No
  timer, no pass on a device event, no pass at startup. The worker loops wait on
  their poke with no timeout at all, so there is no interval left to set.
- Identity stays automatic — one `res:ota-settings` frame plus one
  `GET /api/settings`, at startup and on every connect. It is what tells the
  header whether the machine is there, and a sync has nowhere to store a shot
  until the machines row exists.
- The WebSocket is still held: it is what the header pill reads, and what a
  profile push, a notes write-back and a storage cleanup travel over.
- Automatic cleanup, where it is switched on, now runs after a sync — which is
  the right moment for it.
- **Removed:** `GET /api/device/live` (the 2 Hz telemetry stream), the
  `devicePollIntervalSeconds` setting, and the fake device's `--brew-every`. An
  archive that still holds a row for the removed setting ignores it.

**No live view.** The `/live` page, its nav entry and its `g l` chord are gone,
along with the streaming chart, the live-status store and the device page's
"Right now" and "Warnings" cards. The header pill keeps its four states from the
status poll alone. Chart.js stays for the shot, compare and Set trend charts.

**The shots page is a dataset.**

- **"Sync with machine"** in the header: disabled with a reason when no machine
  is configured or it is unreachable, a spinner while a sync is running —
  whoever started it — and a toast when the one you started finishes, counted
  off the ledger ("Synced: 3 new shots, 1 updated." and what the profiles did, or what the
  machine said when it failed). The subtitle says when the archive was last synced
  ("Last sync 2 minutes ago"), or "Never synced".
- **A drop zone** under the header takes shot and profile exports — `.json`,
  `.slog` or a zip of either — with a file picker for keyboards and phones and a
  result line that unfolds into a row per file.
- **Filters behind one button** with a count of how many are on, instead of
  eight dropdowns across the top of the page. The URL contract is unchanged.
- **Column headers sort**, with an arrow and `aria-sort`; the sort dropdown is
  gone.
- **A column chooser**, remembered per browser. Profile and Curve are off by
  default — a sparkline is a request and a canvas per row, and the profile name
  is the same string on nearly every row — and Set is a column of its own.
- **Rating, notes and Set can be set from a row**: click a star to rate, click
  the lit one to clear, or open a small panel at the end of the row for all
  three. A rating set from the list merges into whatever verdict already exists,
  so taste tags, doses, grind and decision typed on the detail page survive it.
- The list row carries the verdict's rating and notes, and the rating a row
  shows is the verdict's, falling back to the machine's notes card and then to
  the index — the same expression the "rating" sort and the minimum-rating
  filter use, so a page that can set a rating agrees with itself about what the
  rating is.

### Profile drafts and push to the machine

The first thing gaggiclanker writes to a GaggiMate. It turns an analysis's
`profile_patch`, or a hand edit, into a validated profile draft, and — once a
person has approved it — saves it to the display as a **new** profile.

- **Off by default.** `deviceWritesEnabled` gates every write method and is
  re-read on every write, not cached at boot. A device client built without the
  app's gate can write nothing at all, so read-only is what forgetting gives you.
- **Never overwrites in place, never selects on its own.** `save_profile` refuses a profile
  carrying an id — the firmware upserts on the filename — so the machine always assigns its
  own; a replaced profile is saved as a new file first and the old one removed after.
- **A removal is guarded by a fresh load.** The file must still hold exactly what the app last
  recorded; a file that changed is never deleted unseen.
- **A safety policy narrower than the firmware**, tunable from Settings:
  60–100 °C, 0–12 bar, 0–10 ml/s, phases of 0.5–120 s, at most ten of them. It
  clamps and **says what it moved**; anything a clamp cannot fix is refused
  rather than quietly rewritten.
- **Stop conditions need an explicit acknowledgement.** A draft that adds,
  removes or moves a `targets` entry changes how much coffee ends up in the cup,
  and cannot be approved until somebody says they meant it. `9` and `9.0` are not
  a change.
- **Round-trip verified.** The push reads the profile back and compares canonical
  JSON. A mismatch is a stored `failed` draft carrying both documents plus one
  button that deletes the machine's copy.
- **Every attempt audited**, refusals included, and listed on the Device page.
- **A simulator gate**: every profile fixture and one generated draft saved to
  the real firmware compiled natively, read back, brewed, and deleted.
  `scripts/profile_gate.py` runs the same four layers over a file from a shell.

New: `GET/POST /api/profile-drafts` and its discard / refine routes,
`GET /api/device/writes`, proposals on the Profiles page, "Draft profile" on the shot
analysis panel, and "Edit a copy" on a profile version. Migration `0008`.

### Device storage cleanup

The machine is a buffer: its firmware deletes the oldest shot whenever free
space drops below 500 KB, archived or not. This makes shots leave the display
*when the archive has them* instead.

- **A shot is only deleted once this box holds it, intact.** The eligibility
  rule is one function, applied by the plan step so a preview means something and
  by the write gate so it is actually enforced: the shot must be in the archive
  for this machine, not quarantined, and its stored blob must be exactly the
  length its header implies (or already recorded as `incomplete`).
- **A policy, off by default.** `deviceCleanupMode` is `off`, `keep_newest`
  (default 50, at least 5) or `free_space` (default 2048 KB free, at least
  1024 — the firmware's own threshold is 500). `deviceCleanupAuto` runs it after
  each successful index read; without it, cleanup is a button.
- **Oldest first, two a second, stopping on the first refusal.** The firmware
  deletes in id order, so anything else would fight its own retention; the pace
  is because the display's web server is pumped from its main loop.
- **A preview that lists what it will *not* delete, with the reason.** "Why is
  that shot still on my machine" is otherwise unanswerable.
- Every delete is audited in `device_writes`; every run is a `cleanup_runs` row
  carrying both figures (planned and deleted) and the error that stopped it.

### Judgements written back to the machine

Your verdict on a shot, mirrored onto the display's own notes card so the
touchscreen shows it.

- **Off by default and gated twice**: `notesWritebackEnabled` on top of
  `deviceWritesEnabled`. `notesWritebackFields` picks what is sent; `notes` is
  offered and not on by default.
- **The document is the machine's, with our fields laid over it.** Unknown keys
  another client wrote survive; a field not in the policy keeps what the machine
  has. `doseOut` goes as a **string** — the firmware only honours it as an
  override for the index volume when it is one — and `timestamp` is set by us,
  because the firmware never sets it.
- **Newest wins, and a device note is never echoed back.** A verdict is written
  only when it is newer than the machine's card; one that was *seeded* from the
  machine and never edited is never sent. The read path's rule is unchanged: a
  sync can create a judgement, never overwrite one.

New: `GET /api/device/cleanup/plan`, `POST /api/device/cleanup/run`,
`GET /api/device/cleanup/runs`, `GET /api/device/notes/pending`,
`POST /api/device/notes/push`, `POST /api/shots/{id}/notes-writeback`, a Storage
card and a notes card on the Device page, "Sync notes to machine" on a shot, and
six settings under Settings → Machine access. Migration `0010`.

### Knowledge base tiers 2 and 3

The rule tier is a few hundred one-sentence facts. This adds the two tiers
either side of it: the prose behind the rules, retrieved a few passages at a
time, and what this archive has learned about *your* kitchen.

- **Tier 2 — 25 documents, 63 chunks, about 31 000 words.** The gaggimate-mcp
  knowledge files (MIT; see `gaggiclanker/knowledge/seed/docs/ATTRIBUTION.md`),
  split at their H2/H3 headings into 200–600-word chunks and indexed with FTS5
  (BM25, `porter unicode61`, headings weighted ten to one). Seeded on boot with
  the same three-way rule the prompts and the rules have: new documents are
  inserted, unedited ones take the new text, edited ones keep yours and only the
  shipped default moves.
- **A chunk id is a citation.** `heading_path` — e.g.
  `ESPRESSO_BREWING_BASICS#adjustment-strategies/variable-hierarchy` — is derived
  from the headings, so it survives a re-seed, a reset and an upgrade. An
  analysis prints the ones it used and the panel links each straight to the
  passage. Tables and fenced blocks are never split: half a table still looks
  complete, which is worse than no table.
- **Retrieval is deterministic and budgeted.** Queries are built from the
  judgement's taste and balance, the channeling indicators that fired, the
  diagnostic bands that were not normal, then the bean and the style — in that
  order. Top hits merge into a stable total order, at most one chunk per
  document, under `analysisChunkTokenBudget` (default 1500 estimated tokens; 0
  turns retrieval off). The same shot gets the same excerpts twice running.
- **Excerpts are supporting context; the rules stay authoritative.** The prompt
  says so, and `excerpts_used` is checked against what the shot was actually
  given — an invented citation is dropped rather than shown.
- **Tier 3 — learned insights.** Scoped by any of bean, roast level, process,
  origin, grinder, profile style and machine; an insight applies when **every**
  key it states matches. The analyzer proposes at most two per analysis, with
  the shots they were drawn from, and they land **unconfirmed**: nothing reaches
  a later prompt until somebody presses confirm, because a model that
  generalises from one shot and is then believed by the next analysis has
  manufactured its own evidence. Confirmed ones are rendered above the rules as
  "what you have learned".

New: `GET /api/knowledge/docs`, `GET|PUT /api/knowledge/docs/{slug}`,
`POST /api/knowledge/docs/{slug}/reset`, `GET /api/knowledge/search`,
`GET|POST /api/knowledge/insights`, `PATCH|DELETE /api/knowledge/insights/{id}`,
a Knowledge page with Rules / Docs / Insights tabs, "Reference excerpts" and
"Proposed insights" on the analysis panel, learned insights on the Set page, and
one setting (`analysisChunkTokenBudget`). Migrations `0011` and `0012`.

### A starting point for a new coffee

The first shot with a coffee nobody has brewed, answered from what this archive
already knows rather than from a chart.

- **Similar past Sets, by SQL and for free.** The wizard shows what you have
  already brewed on *this grinder* that resembles the new coffee — same roast
  level, same process, same origin — with how each one actually went: shots,
  mean rating, mean execution score, ratio and time. It costs no tokens and it
  is worth reading on its own. A recipe with no shots behind it is never
  offered: it records an intention, not a result.
- **Three options, not one.** Conservative, recommended, adventurous, each with
  a grind, a dose, a yield, a temperature, a profile and a rationale citing the
  rules and Sets it leaned on. Nobody knows what a new coffee wants yet, and a
  single confident answer hides that.
- **It will not invent a grind number.** A grinder's scale is arbitrary and
  there is no conversion between two of them, so a number is offered only when
  your usual setting or a past Set on the same grinder anchors it. Otherwise the
  answer is relative — "a little finer than your usual" — and the card says so.
- **Taking one creates the Set** with `origin = starting_point`, and — when the
  option authored a whole profile — a draft through the same schema, safety
  policy and clamp a hand-typed one goes through. Nothing is pushed to the
  machine; a person approves it. An option whose profile the policy refuses is
  rejected with every violation and creates nothing at all.
- **The Beans page has a shortcut** straight into the wizard for the coffee you
  are looking at, and the chat can ask for one through the `starting_point` tool.

### Fixed

- **Phase bands no longer hide the shot curves.** On the shot chart every
  second phase was shaded with an opaque colour and painted on top of the
  lines, so a soak or a long decline blanked out pressure, flow, weight and
  temperature across its whole span. The bands now sit behind the curves in a
  faint, see-through wash of their own (`--chart-band`, one per palette); the
  phase names and the dashed end-of-shot line still draw above them.

## [0.1.0] — 2026-09-11

The prototype. It archives every shot a GaggiMate has taken, shows the curves
and deterministic diagnostics, lets you judge each shot and group shots into
versioned Sets, and produces a per-shot LLM analysis you can act on.

### The archive

- Every shot the machine has, with its **raw `.slog` bytes kept verbatim**. A
  shot that fails to parse is stored quarantined rather than dropped: the
  machine deletes old shots under storage pressure, so by the time a parser bug
  is fixed its copy is gone.
- A persistent WebSocket to the display board with reconnect and backoff, plus
  index polling as the safety net behind `evt:history-shot-saved`. Exactly one
  socket, because the firmware allows three clients in total and the machine's
  own browser UI is one of them.
- Profiles, profile versions, the device's own shot notes and the machine's
  identity, all mirrored.
- **The device client is read-only** — ten read methods and nothing else,
  enforced by a test rather than by convention.
- Import of the web UI's JSON exports, from a file, a folder or a zip, on the
  command line (`gaggiclanker import`) or through the UI. That is the only way
  back for a shot the machine has already deleted.

### Reading a shot

- Full curves at the machine's own sample interval, with the phases the firmware
  recorded.
- Deterministic diagnostics — resistance, channeling risk, temperature
  stability, pressure and flow adherence, overshoot — with band labels, and an
  execution score that says which component capped it. Everything
  pressure-derived is gated on the board's capability flag, because Standard
  boards report zero.
- A live view that follows a brew at 2 Hz and links to the saved shot when the
  file lands.
- Judgement per shot: rating, balance, taste tags from a fixed vocabulary, dose
  in and out, grind, notes and a decision.
- **Sets**: a bean, a grinder, a machine, a profile and a recipe, versioned.
  Shots are assigned to the version that was current when they were pulled, and
  the trend chart reads across versions.

### The analysis

- One structured LLM call per shot, given the diagnostics with their band
  labels, the Set, the previous five shots with your verdict on each and the
  advice that followed them, and the knowledge rules that match. It answers with
  a diagnosis and prioritised suggestions.
- Providers: `claude_code` (the default — spends a Claude subscription, no API
  key), `anthropic`, `openrouter`, `openai`, `ollama`, `lmstudio` and any other
  OpenAI-compatible gateway. Each has its own credential and none is ever lent
  to another.
- An editable **knowledge tier** of dial-in heuristics, each with its source, a
  confidence and a switch. The model names the rules it used and the analysis
  links back to them, which is how a rule that misleads gets found. Adapted from
  [gaggimate-barista](https://github.com/chall-tech/gaggimate-barista) (Charlie
  Hall, MIT).
- Accepting a suggestion creates a new Set version with that one field changed
  and the old version as its parent, so "did the advice help" is a question the
  trend chart answers.
- The call runs as a background task, not inside the request. A failure is a
  stored row carrying the provider's error, never an exception; a run cut off by
  a restart is marked `interrupted` at the next boot.
- Prompts are rows in the database, seeded from YAML and editable from the
  Settings page; an edit takes effect on the next call.

### Running it

- A multi-stage image: the front-end build, a uv-installed venv, and a runtime
  stage carrying neither node nor a compiler. Non-root, `cap_drop: ALL` with an
  empty capability bounding set, `no-new-privileges`, and a healthcheck that
  needs no extra package.
- `compose.yml` defaults to host networking, with a bridge alternative
  documented next to it.
- One SQLite file under `DATA_DIR`, so a backup is a file copy. `POST
  /api/backup` uses `VACUUM INTO`, which is consistent while the app is running.
- `.env.example` documents every variable, and a test asserts it stays in step
  with the settings registry in both directions.

### Security

- **Optional single-user authentication**, off unless `AUTH_USER` and a password
  are set. HS256 bearer tokens with a server-side session row, so signing out
  revokes rather than merely forgets; argon2id password hashing with a
  constant-time compare; five failed sign-ins per address buy a sixty-second
  lock; a public status probe so the UI can offer a sign-in form before the
  first 401. The guard covers every `/api/*` route including the event streams
  and the OpenAPI document, with `/health` and the web bundle public.
- **Auth fails closed.** A stored password hash that argon2 cannot verify leaves
  authentication *on* and refusing every sign-in, with the fix named in the log
  — never off. `POST /api/auth/password` is the only way a browser sets the
  password: it takes the plain value and hashes it on the server, and the
  settings API refuses the hash field outright. Changing the password or the
  username revokes every open session.
- CORS off by default; request body limits; `nosniff`, `DENY` and
  `no-referrer` on every response; a log sanitiser that redacts secrets by field
  name and by value shape; a rate limit on the two routes that spend money.
- Every response carries a request id, in a header and in the body, matching
  every log line for that request.

### Known limitations

- **Only profiles are ever written to the machine, and only by a sync with the Writes
  switch on.** No settings, no mode changes, no shot deletes. The four-layer write path
  in `docs/safety-layers.md` guards every write.
- One user, no roles, and no TLS of its own. Put it behind a reverse proxy if it
  is going to face the internet.
- `cost_estimate` is recorded as NULL: nothing here knows what a token costs on
  the provider you happen to be using, and a made-up figure in a money column is
  worse than an empty one.
- The archive is a single SQLite file on one box. There is no replication and no
  off-site copy but the one you make.
