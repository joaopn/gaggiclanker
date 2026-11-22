---
title: Shots and Recipes
parent: User guide
nav_order: 2
---
# Shots and Recipes

## The pages

*How this works: [Sync and the archive](sync-and-the-archive.md), [Shot checks and review](shot-checks-and-review.md).*

The sidebar has six rows: Shots, Chat, Recipes, Profiles, **Brew setup** (Beans, Hardware,
Taste wheel) and **Settings** (one page per heading, with Knowledge just before Prompts).
The Device page has no row: the machine status in the top bar opens it from every page. A group opens when you click it, and on its own
when you are on one of its pages; one you open by hand stays open next time. The
pages, in the order the sidebar lists them:

| Page | `g` | What it is |
| --- | --- | --- |
| **Shots** | `g s` | The archive: the list, the filters, one shot with its curve and diagnostics. A row opens in place with the shot page's judgement and curves boxes, laid out as on the page: the full judgement across the top (saved with its button, the notes in its right-hand column) and the curves with their toggles and downloads on a row of their own below it. Each row's Rating column is the stars, coloured by their band, its Balance column records Sour, Balanced or Bitter (filled is your verdict, outlined is the machine's notes card), its Discard column is an outlined Discard button that you click for "this shot went wrong, do not count it" (it then reads Discarded, and a click clears it; the judgement form has the same as a "Discard this shot" switch), and **Hide discarded** (ticked by default, on the line under the buttons, remembered in this browser) leaves the discarded ones out of the list; the Recipe column can be dragged narrower. A first visit shows Recipe, Time, Duration, Yield, Curve check, Rating, Balance, Discard and AI Review; Profile, Notes, Curve, Drink and Flags are in the column chooser, and a layout you chose is kept as you made it. The optional Curve column has a button in its heading that chooses which of the nine series it draws (pressure and puck flow by default) and in which theme colour, remembered per browser. A shot is reviewed from its own page, and the review is shown only there. The filters narrow by date, profile, Recipe, rating, source and readability; a Recipe's experiment log links each version's shot count straight at *that version's* shots, and the filter says which version is on and removes it in one click. The import drop zone is here; Sync is in the top bar, on every page. A shot's page names its profile as it is called now, linked to it, and says what the machine called it when the shot was brewed ("brewed as") when that was another name. A new shot is filed under the one Recipe that brews its profile: exactly one Recipe set to collect shots, whose current version names that profile (never over a Recipe you picked). **Match by profile** runs the same rule over the shots already waiting; a shot's own page has the button too. Above the table, **Chat about** has one button per Recipe you are brewing (not archived, not being designed), labelled with its current version: it opens or continues that version's conversation with a question already typed, so after judging the shots in the table you only press Enter. |
| **Chat** | `g c` | The tool-using conversation, with a badge per Recipe above it. A badge lists that Recipe's conversations; New there starts one already pointed at that Recipe. |
| **Recipes** | `g e` | How you dial in one bean: the bean, the grinder, a profile that belongs to the Recipe alone, and the numbers on top (grind, dose, target yield), versioned. The Recipe follows its profile, so a new active profile version is a new Recipe version; the page explains this in **How a Recipe works**, open while no Recipe is being dialled in. Each Recipe has the trend across versions and the experiment log: what each version changed, what you predicted it would do, how its shots were rated (Good, Neutral, Bad, Discarded), and whether the prediction held. One click rolls an old version back. Each Recipe says whether new shots on its profile are filed under it — any number of Recipes can, which is how two beans on two grinders both collect — and a Recipe is archived when you have finished with its bean. |
| **Profiles** | `g p` | One list of every profile you have had. Each row has two switches, **On the machine** (the next sync puts it there or removes it) and **Starred** (the machine's home-screen carousel, kept while the profile is off), what it brews, where it stands on the machine and what the next sync will do about it; profiles that are off are hidden unless you ask. The two profiles the machine keeps for itself (its backflush and descale, seeded by firmware 1.10 at every boot and refused by its delete) always read **On the machine**: the switch cannot be changed and the row says "Kept by the machine". The machine shows at most ten starred profiles, so an eleventh star is refused with the limit named. A row opens to its versions, newest first: when each was made and where it came from, its shots and Recipes, its information (the first as a summary, every later one as what changed from the version before it), **Make active** and **Duplicate Profile** (a new profile from that version, under a name you type; see [Putting a profile on the machine](profiles.md#putting-a-profile-on-the-machine)). A version the agent proposed sits above them as the proposal card ([Putting a profile on the machine](profiles.md#putting-a-profile-on-the-machine)): marked **Proposed**, with **Approve and sync** (**Approve** with Writes off) and **Decline**; a proposed new profile is a row of its own, with its Name field. An open profile shows its name with **Rename**, which opens a Name field (Enter renames, Escape cancels; a pinned copy can be renamed too, since a pin freezes what it brews, not what it is called). Only you rename a profile; the machine gets the name at the next sync with Writes on, and a row says so ("Becomes <name> on the machine at the next sync") until then. A profile a Recipe brews shows the Recipe's current version after its name on the machine ("Alma Lever v1.3"); the name you edit never carries it, and a new version renames the machine's file in place at the next sync. A name given at the machine is taken at the next sync, with a note under **On the machine** until the sync after. Two name conflicts wait for your choice, each as a line on the row: the machine's new name is another profile's (**Keep** your name, which renames it back on the machine, or **Rename to …**), or the name was changed on both sides (**Keep** either). A profile whose file was edited on the machine shows **Conflict** and opens on both sides to choose from. After a suspected reset one banner asks whether to put the profiles back. |
| **Beans** | `g b` | The coffees: roaster, origin, process, roast level, decaf, acidity, intensity and sweetness (each a clickable 1-to-5 scale; click the chosen step again to clear it) and a free-form description. Roaster and origin suggest the values already recorded; a coffee is archived when you stop buying it, and one no Recipe uses can be deleted. Each coffee exports as a JSON file, and **Import JSON** reads one back: the file's id updates that coffee with the fields it carries, anything else adds a new one. |
| **Hardware** | `g h` | The machine — what it says it is, and the name and notes you give it — and the grinders. |
| **Taste wheel** | `g w` | The SCA/WCR Coffee Taster's Flavor Wheel, all three tiers. Pick which of its notes the shot panel offers, one list for taste and one for aroma. |
| **Settings** | `g ,` | One page of collapsible cards per heading, the ones you must fill in first: Machine access and LLM, then Authentication, Prompts, Profile safety, System and Import. The LLM page also holds the knowledge and chat budgets. |
| **Knowledge** | `g k` | Under Settings. The dial-in rules, the prose documents, and your **general** insights: the ones you write by hand, any agent-written one the app could not place on a single Recipe, and the ones you approve from **Find patterns across Recipes**. Insights learned in a Recipe's conversation live on that Recipe's page instead. |
| **Device** | | No sidebar row: the machine status in the top bar opens it from every page (while the machine is connected, the status also shows the boiler's temperature, read every fifteen seconds). What the machine is (its versions and its connection), then the record of the exchanges with it: the last run of each sync pass and what the archive holds (the sync itself starts from **Sync**, first in the top bar), what the last sync did to its profiles, and every write this box has attempted on it, refused ones included. Nothing but a profile is ever written to the machine. |

The sidebar folds. The button at the foot of it, or the `[` chord, collapses it
to an icon rail and back; the choice is remembered in the browser. Folded, every
entry keeps its name — a tooltip for a mouse, the accessible name for everything
else — so nothing is lost but the fourteen rems.

The old import page is now the drop zone on the Shots page, and the old drafts
page is Profiles, where a proposed profile now opens on its own row; `/import` and `/drafts` still resolve,
by redirecting. The old Sync page's address, `/sync`, and its anchors redirect to the
same cards on the Device page.

## The experiment log

*How this works: [Recipes and versions](recipes-and-versions.md).*

A Recipe page is a record of experiments, one per version. A version's numbers are
five things: the **profile** version, the grind as text and as a number, the
dose and the target yield. A Recipe has exactly one profile, and follows it: whenever the profile gets a new active version
(a change approved, **Make active** on the Profiles page, the machine's side kept after an edit
there) the Recipe records a new version naming it, in the same step. **Change something** on the
Recipe page has no profile to choose, except for a Recipe that has none (one being designed, or an old
one that never named a profile), which takes a profile no Recipe has. That is also what keeps the
next shots landing in the Recipe, because a shot joins the one Recipe that brews the profile it was
pulled with. To try a bean on a copy of a Recipe's profile, use **Duplicate Recipe**, or pick that profile in New Recipe, which duplicates it.

**A version also says what it is for: its drink and drink notes.** The drink is what is
added to the shot after it leaves the portafilter (Espresso, Americano, Cappuccino, Latte or
Other) and the notes say how the drink is made around it, in your words. They are inherited
like the numbers, so each new version keeps them until it changes them (empty notes take them
away; a drink once stated is never unstated). They are not among the five numbers and play no
part in what counts as a repeat. A version made before drinks were recorded states no drink,
which is not Espresso. Changing the drink suggests a major version, and the agent reads the
drink's knowledge entry whenever it is asked about the Recipe. New Recipe asks for the drink
(Espresso to begin with) and the notes; the drink shows as a badge on the Recipe's card, beside
its name on its page and on every version of the log, and the notes under the page's heading.
**Change something** starts those two fields filled with the current version's, so that clearing
the notes can remove them; only what you change is recorded, and another drink ticks Major change
for you to keep or untick. The shots table has a Drink column, off until you switch it on.

**The brew temperature is not one of the five**, and there is nowhere to type
one: the machine heats to what the profile says, so a number written on a Recipe
would have been a claim about a document it does not control. Both forms
show what the picked profile brews at, read-only, and changing it means changing
the profile — edit it on the machine and record the new version here, or draft
one on the Profiles page. When a version switches to a profile that brews at a
different temperature, the log shows "Temperature 93 → 94 °C" beside the profile
change, marked as coming from it.

Each version says what changed against the version before it, what you were
trying, and — optionally —
what you **predicted** it would do, compared to which version. Once the shots
are in and you have rated them, you record the **outcome**:
held, partly held, failed or inconclusive, with a line saying why. Above the log
sits the only number that matters, "6 of 10 predictions held".

**Versions are named v1, v1.1, v1.2, v2.** Dialling in — the grind, the dose,
the yield, a profile draft that only tunes a parameter such as a degree of
temperature — is a **minor** version: it keeps the major and takes the next
minor (v1.2 → v1.3). A functional change to what the profile does is a **major**
version: the next whole number (v1.2 → v2). You decide which, with the **Major
change** box on the Add a version form and on a change card before you accept it; each
button names the version it will record ("Accept as v1.3", "Accept as v2"). The box
starts off, so the version is a minor one. A Recipe has one profile for good, so a new
version never switches profiles. A Recipe
follows its profile: a profile version put, made active or kept from the machine for
the Recipe's profile becomes a new minor version of the Recipe in the same step, and
making an older Recipe version current makes its profile version the active one without
adding a version. The agent may suggest major on its card, with its reason
beside the box, but the box is yours. Versions recorded before this existed
keep their numbers: v3 is still v3. Everywhere a version is named — the log,
the chat, the agent's context and tools, the shots table — it is by this name.

Two rules keep that number honest:

* **A prediction can only be written before the version's first shot**, and
  before its outcome has been recorded. After either, the app refuses it and
  says which. A guess typed once the cup has been tasted is a memory, and a
  track record built from memories measures nothing; a guess rewritten under a
  grade would leave that grade attached to something nobody ever predicted, so
  the grade has to be cleared first.
* **The prediction is always shown.** On the shot page and in the shots list's
  panel, a shot whose version predicted something shows the words, and what it
  was compared to, with nothing to click first.

The outcome is the other way round: it can be changed or taken back whenever you
like, because a second opinion about a grade is ordinary.

**The agent can propose the outcome for you to accept.** At the end of its grade
a conversation about a version proposes one outcome for the whole version,
worked out from all its counted (rated, not discarded) shots, with the per-claim lines
under it. It shows as a card in the chat: **Accept** records it as written,
**Record another outcome** records your own choice of the four, **Dismiss**
records nothing and may say why. Nothing is recorded until you press one, the
Recipe page shows a waiting proposal beside the outcome and never as one, and
nothing an agent graded reaches a later conversation, the experiment log or the
track record unless you accepted it. You can also set or clear the outcome on the
Recipe page as before; a waiting card then shows both. When the same answer also
proposes the next version, accepting that version records the waiting grade
first, in the same step: one press, as long as the outcome is still open; an
outcome you recorded yourself is left alone and the grade stays waiting. If you dismissed the grade, the version
cannot be accepted until the version is graded.

## The spread: how much your shots vary anyway

*How this works: [Recipes and versions](recipes-and-versions.md#the-spread-how-much-the-shots-vary-anyway).*

Above the log the Recipe page says how much its shots differ **when nothing in the
version's numbers changed** — "Shot time ±1.8 s · from 9 repeat shots at 3 settings". It is the number
every comparison on that page is held against, because "31 s against 34 s" means
nothing until you know whether three seconds is a lot for this grinder, this
basket and this bag. It is arithmetic the app does, the same way every time; no
model is involved.

**What counts as a repeat.** Shots brewed with the same five fields —
profile version, grind text, grind number, dose, target yield — are repeats of
each other, whichever version they were filed under. That means shots filed
under a version after you went back to it count as repeats of its earlier shots,
and so do the shots of a version that only changed the words. Each group's
shots are measured against their own average and those distances are pooled
across the whole Recipe, so many versions of two or three shots each still add up
to one usable figure. A group of one contributes nothing: a single shot has no
distance from itself.

**Which shots count.** Everything filed under the Recipe that is not quarantined,
not incomplete and not discarded. Shots you have not rated count too —
they are plain data, and leaving them out would make the number depend on how
diligent you have been with the stars.

**The stars are the judgement.** A shot's rating is the one the Rating column shows (your
verdict's, then the machine's notes card, then the machine's index; no rating, or 0, is none).
Where shots are counted it is read in words: **Good** is four or five stars, **Neutral** is three,
**Bad** is one or two. The stars are coloured by the same bands (green, grey, amber). Good,
Neutral and Bad are never a control and never stored: they are the rating, worked out. **Discard**
is its own column and its own meaning: "this shot went wrong, do not count it". It is independent
of the stars (a discarded shot keeps them) and is never worked out from a rating. The counts, the
gold standard (the Good shots), what a prediction can be graded on (any rated shot that is not
discarded), the way back to a version that worked (one with a Good shot) and the agent's shot search
all read the shot this way, and all leave a discarded shot out first.

**Eight measures**, each used only where the archive already holds it: shot time,
first drip (when coffee first reached the cup, from the scale), first puck flow
(the pump model's estimate, which every shot with a pressure sensor has), yield,
peak pressure, cup flow (what reached the cup, from the scale), puck flow and your
rating. A shot with no scale has no cup measures, and a cup measure is never pooled
with a puck one. The
yield is the one you typed into the judgement if you did, then the machine's
final weight, then the volume its own index recorded — the same order the
starting-point wizard scores a version by, because a machine with no scale
records nothing and the only yield that exists is the one you wrote down. A
measure your machine or your scale records nothing for is left off the block
rather than carried as an empty line, and so is a number the archive wrote as
zero to mean "there was nothing to average".

**Before it is measured.** It takes three degrees of freedom — four shots pulled
with one set of numbers, or two with three shots and two, since each
spends one degree on its own average — before the figure is worth trusting. Until then the
line says "not measured yet" and names a conservative floor instead: 2 s for the
shot time, 1 s for either first drip, 1 g for the yield, 0.3 bar for the peak
pressure, 0.2 g/s for the cup flow, 0.2 ml/s for the puck flow and half a star
for the rating. These are
first numbers, to be tuned with use.

**The evidence behind a prediction.** Every version that predicted something
carries an **Evidence** disclosure in the log: all of that version's counted
shots against all of the compared version's, measure by measure, with the mean
and the count on each side, the difference with its sign, and what that
difference shows — *beyond the spread* or *inside it*, and what it was held
against. All the shots on both sides, never a chosen one. The yardstick is the
floor while the spread is not measured yet, and two standard errors of the
difference once it is (`2 × spread × sqrt(1/n + 1/n_other)`), never less than
the floor: a difference exactly the size of the yardstick counts as inside it,
and the comparison is made on the numbers rather than on what is printed. A
difference and its yardstick are written one decimal finer than the means they
came from, so a row can never read "+2.0 s, beyond 2.0 s" when what happened was
"+2.04 s, beyond 2.00 s".
Under the table sit the balance counts and the Good / Neutral / Bad / not rated
counts for both sides, as plain facts with no verdict on them. It is closed
unless the prediction is still ungraded and there are shots to grade it with.
None of this appears on the shot page, which shows the prediction and not a table
of what the version has been doing.

Versions are listed by when they were made, newest first, with the one the Recipe
is on marked **current** and each naming the version it was made from. A name is
an identifier, like a tag, not a position: v1.2 may have been made after v2.

**Go back to this version** puts the Recipe back on that version, as it was.
Nothing new is recorded: its prediction, shots and outcome stay, new shots are
filed under it, and a recorded outcome is not reopened. The log gets one line,
"Went back to v1.2 (from v2.1)", with the date and an optional note. The next
change continues that line: from v2.1 back to v1.2, the next minor is v1.3 (or
the next free minor if that is taken), and the next major is the highest major
plus one. When the current version has Bad shots and no Good one, the page offers
the way back to the nearest version behind it, on the line it was made from, that has a
Good shot.
**Going back changes the machine, by the usual route.** The Recipe's profile goes back to what
that version brews (it becomes the profile's active version), and the next sync puts it on
the machine if the profile is switched on and writes are on. A Recipe has one profile, so going
back never moves it to another one.

Each version's shot count is a link into the shots list narrowed to **that
version**, not to the whole Recipe: the Good / Neutral / Bad / Discarded counts beside it
are that version's too.

Versions off the line you are now brewing are marked **dead ends** and muted,
still fully readable: they were real attempts. The line is read backwards from
the current version through the version each was made from, as in version
control, and everything it does not pass through is a dead end. Go back from
v2.1 to v1.1 and v2 and v2.1 are dead ends; go forward again onto one of them
and the muting follows: what is live is whatever the current version actually came
from.
