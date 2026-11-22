---
title: Review and chat
---
# Review and chat

## LLM settings

A review, the chat and the drafts go through one provider, chosen with `llmProvider` under
**Settings → LLM**:
`claude_code` (the default — it runs the Claude Code CLI against a Claude
subscription, so there is no API key to buy), `anthropic`, `openrouter`,
`openai`, `ollama`, `lmstudio`, or `openai_compatible` for any other gateway.

Each provider has its own credential, entered under **Settings → LLM** and kept
in the database only, and none is ever lent to another: `llmApiKey` for the
OpenAI-compatible presets, `anthropicApiKey`, `claudeCodeOauthToken`. None of
them is read from the environment. For `claude_code` the token comes from
`claude setup-token` — an interactive `claude login` is not enough, because every
call runs the CLI with a scratch `HOME` and an environment that carries only the
stored token, so nothing on the box leaks into the prompt, and that hides
`~/.claude` too. The Docker image ships the CLI; a source checkout needs it
installed (`npm install -g @anthropic-ai/claude-code`) or `claudeCodeBin`
pointed at it. The image's CLI is pinned; to take a newer (or older) release
without rebuilding, the Claude Code panel under **Settings → LLM** installs the
`stable` or `latest` channel, or an exact version, straight from npm into the
data directory. The release is checked against the registry's sha512 and run
once before the app switches to it, it survives restarts, and it steps aside
once a newer image carries that release or a later one. It is used while
`claudeCodeBin` is left at `claude`; **Use the image's version** removes it.
Nothing updates on its own. **Validate credentials** tries what the form holds, so a pasted
token can be checked before it is saved, and for `claude_code` it makes one
one-word call to haiku: presence alone (`claude auth status`) cannot tell a
working token from a revoked one.

`modelDefault` is the default model; `modelReview`, `modelDraft`, `modelChat`,
`modelStartingPoint` and `modelPatterns` override it per kind of call, and an empty value lets the
provider choose. `llmTimeoutSeconds` bounds one attempt, and
`llmRateLimitRetries` is a process-wide budget: when the provider
throttles the account the whole app stops rather than failing every queued shot
in turn, and Settings → LLM has the button that starts it again. Settings →
Prompts edits the prompts themselves — they are rows in the database, seeded
from the YAML files in `gaggiclanker/prompts/`, and an edit takes effect on the
next call without a restart.

## The review

A **review** is a model's look at one shot, and it is yours to correct. It is one structured
call, started only by a click on one shot (the **Review** button in the shots table, or the shot
page): no chat tool, batch, timer or sync step starts one, and a shot you discarded or
that never parsed is never reviewed. The model is handed the shot's own information with its
checks first (every line the shot tools can show, whatever you set under Settings → Shot
information, except your judgement, the note typed on the machine and your label), the Recipe
version's numbers and its prediction, the free-text expectations of the profile's signature in
force, the profile the shot brewed, the detected shot style, and the knowledge rules and
reference excerpts its telemetry selects. It is never shown your judgement, another shot, an
earlier review or a conversation.

It writes **claims**, and nothing else about the cup: no taste prediction, no advice, no
proposal. Each claim is tied to a window of the shot (a phase, a span between two moments, or
the whole shot, so the curve can highlight it), carries a fault word from the fixed list or none,
one sentence with no figures in it, and one to three metric-language expressions. **The numbers
are the server's**: it evaluates every expression on the shot and stores the value, the unit,
the kind of channel and whether the expression's own comparison held, so a figure beside a claim
is one the same expression gives on `POST /api/shots/{id}/evaluate`, never one the model typed.
A claim whose comparison fails, or whose evidence could not be measured at all, is kept and
marked as not borne out by the numbers. The review also answers each free-text expectation of
the signature in force (held or not, and where), and, when the Recipe version was filed with a
prediction, says how the shot moved against it (as predicted, partly, against, not shown) plus
a one-sentence summary for you.

**Each claim is kept unless you reject it.** There is nothing to confirm: **Reject** takes a
claim off the list and out of the chat, **Restore** (behind "N rejected · show") puts it back,
and the prediction stance is shown like any claim. Only the newest finished review of a shot can
be answered (the one in force: a newer review that is running, failed or was interrupted changes
only the badge's words, and replaces it only when it finishes), and a review that finishes sets
the old one aside, rejected claims included; **Review again** asks once that it replaces the
current one.

**Two columns, two jobs.** The shots table's **Curve check** column is only the deterministic
checks and warnings (`ramp: early yield +1`), worked out whenever the shot is read from its
stored numbers, its filing and its profile version's signature in force, on every shot and with
no click; a review never changes it, and a free-text expectation is not part of it ("checked by
the review"). Its colour is the first entry's severity and it sorts by it. With no entry it says a
green `Pass` (the shot's profile has a signature in force and at least one expectation was measured,
none in the badge) or a grey `Unchecked` (no signature in force on the profile and no warning, or a
signature whose checks measured nothing); the sort puts those after the entries, in that order.
The **AI Review** column (last before Flags) is only what the model wrote: a Review button, `Reviewing…`, a grey
`Failed to run` with Retry, or
the model's faults in the same `phase: fault +N` form, built by code from claims you did not
reject and whose numbers bear them out (a failed free-text expectation, red for critical and amber
for important, and a claim that carries a fault word, amber; a claim the numbers do not bear out
stays in the Review box, marked, and in what the chat is told, but not in the badge or its "+N"), or a green `As intended` (a signature in force and no fault)
or a grey `No faults`; nothing for a shot nobody can review. It sorts faults first, then
`Failed to run`, `Reviewing…`, `No faults`, shots not reviewed yet, `As intended`, and last the
shots nobody can review. Pressing a reviewed badge opens the row with its Review box expanded.

**The boxes.** The shot page and the open row draw the same boxes in the same order: Your
judgement (open), the version's prediction (it does not fold), Curves, Review and Curve check
(folded). The Curve check box is always there; with nothing to list it says one plain line. Each box's
state is remembered per browser; pinning a claim opens Curves, and a Curve check line for a
free-text expectation opens Review at its answer.

**What the chat is told is every claim you did not reject.** The **Review** group of the catalogue
(three items, all base) says whether a shot was reviewed and how many claims were kept or
rejected, lists the kept claims (free-text answers among them) with the numbers behind them and
says how the shot moved against its prediction. The summary and a rejected claim never reach
it. The SQL tool's `v_review_claims` holds the claims of a shot's newest finished review that
were not rejected, and `v_reviews` only counts. The glossary and the chat's rules both say what a
review is: a model's claims about one shot, weighed below the measured numbers and your
judgement, never on its own a reason to change a Recipe.

The knowledge rules are on the **Knowledge** page: a small tier of dial-in
heuristics — temperature by roast, the pressure matrix by roast and process,
ratio and time by style,
taste → suspect, telemetry → cause — each with its source and confidence, each
editable, each with a switch. They are adapted from
[gaggimate-barista](https://github.com/chall-tech/gaggimate-barista) (Charlie
Hall, MIT) by way of gaggimate-mcp; the attribution is in the seed file.

The call runs as a background task rather than inside the request: pressing the
button answers at once with a `running` row and the page follows the event
stream (`review.started`, `review.finished`, `review.failed`, and `review.answered` when a
claim is rejected or restored, so another tab follows), so a restart cannot kill a review with
the browser still waiting on it. Pressing it twice while one runs gets the same run back rather
than paying for two, and the database allows only one running review per shot.

A failed review is a stored row carrying the provider's error code rather than
an exception, and a run cut off by a restart is marked `interrupted` at the next
boot — neither silently disappears.

## The chat

The **Chat** page (`g c`) is the other half of the LLM layer, and it is the
opposite shape from a review: instead of one call with everything in front
of it, the model is given a set of tools and asks the archive its own questions.

**A Recipe is a scope, not a filing cabinet.** Above the conversation is a row of
badges: **General** first, then every Recipe you have not archived, with its current
version and how many conversations it holds — including the Recipes nobody has asked
about yet, because an empty list with a **New conversation** button in it is how
you start. Click a badge and its conversations are listed under the row, one per
line. Conversations about a Recipe you have since archived stay readable behind
**Show archived Recipes** at the end of the row, which adds a badge per archived Recipe. The page opens on the newest Recipe (the one you made
last) unless a link or an open conversation names another, and a question typed
before you pick a conversation goes to the open badge's Recipe.

**Keep a conversation.** With a conversation open, **Download log** in its header
saves it as one JSON file (`chat-<id>-<title>.json`,
`GET /api/chat/threads/{id}/transcript`, sent with your sign-in like any other
call). It holds every message in order with its time and run, the tools the
agent called with their arguments, what they answered, and a record per run
with the model, its tokens and any error. It leaves out what you never typed:
the instructions and the context the app gives the agent each turn (the Recipe's
ledger and its recent shots, the base shot glossary) are not stored with the
conversation and so are not in the file. (The extended glossary is part of the
stored answer to an extended read, so it is reduced along with
that answer, as described next.) The answer of a tool that renders
shots (`get_shot`, `get_shot_extended`, `get_shot_full`, `compare_shots`,
`list_recipe_shots`), or one the provider could not pair with its call, is reduced
to its size, since that is where whole shot renderings arrive; every other
answer, and every refused or failed one, is kept word for word. If the button
says the download was blocked before it reached the app, a content-blocking
extension dropped the request: allow this site in it.

A conversation in a Recipe's list is about **one version of that Recipe** — the
change being argued — and it can see that Recipe and nothing else: its versions
with their predictions and outcomes, its shots, its spread, the knowledge base
and the confirmed insights that apply. It cannot run archive-wide SQL, list your
other coffees, or read a shot filed under another Recipe; a tool that would is not
offered to it, and a shot of somebody else's Recipe is refused in the same words
whether or not it exists. What it *can* do is propose how this version turned out, propose the next
version of this Recipe and record what has been learned about it — each a card
waiting for you to answer.

**A proposed change is a question, not a change.** The agent writes down one
change and what it expects that change to do; the Recipe stays exactly where it is
until you press **Accept**, and your next shot is still filed under the version
in the hopper. The card appears in the conversation and again above the
experiment log, with **Accept** and **Decline** on it, and it says what would
move, why, and what is predicted — *compared to v4, expect 3 to 5 s longer and
less sour*. Accepting records the change as the Recipe's next version, with that
prediction on it, and sends nothing to the machine. The accepted card says what
you do instead: for a grind, dose or yield change the profile on the machine is
already the right one, so there is nothing to push — *set the grinder to 1 and
brew; the next shots on this profile are filed under v2 by themselves*. A change
to another profile says to select it on the machine, and the agent can only
propose a profile the machine has (`list_profiles` says which); any other goes
through a profile draft. Declining creates nothing, and the note you leave is
what the next conversation is told.

The agent cannot dodge any of that. A change with no prediction is refused, and
so is one that moves two things at once unless it says why they cannot be
separated — and then it has to tell you the prediction cannot say which of them
did anything. While the current version's own prediction is still ungraded it
cannot propose the next change at all: it grades that one with you, or asks for
another shot on the same version, which needs no new version. Only one proposal waits
at a time.

**An insight belongs to the Recipe it was learned in.** What the agent learns in a
Recipe's conversation is stored with that Recipe and the version the conversation was
about, shown as a card in the chat with **Add** and **Dismiss**, and listed on
that Recipe's page under the version it was learned at (waiting ones with their
buttons, added ones with **Take back**). Added, it is told to that Recipe's later
conversations and to no other Recipe's, even one on the same bean and grinder; the
design chat of a new Recipe and Review never see it. Nothing waiting or dismissed
reaches a prompt. The Knowledge page holds general knowledge only: what you write
by hand there, and any agent-written one the app could not place on a single Recipe;
those keep matching by bean, grinder, roast and process and still reach every
conversation whose Recipe they match. The first start of this release placed the
agent-written insights you already had about one coffee on the one Recipe each fits
(its scope names a bean, or is empty with evidence shots; it matches that Recipe and
one of its evidence shots is filed there); anything scoped only by equipment, roast,
process or origin, and anything that fits none or several Recipes, stayed general.

**What an insight rests on, and what happens when it ages.** An insight names the
shots it comes from and the versions of its Recipe whose recorded outcome it depends
on. The Recipe page and the chat card show each version with its outcome **as it
stands now**, and when you re-recorded or cleared that outcome after the insight
was written they show the old one beside it with a "changed since" mark ("held →
failed", "no outcome now"); the outcome it had when written is never rewritten. An
insight has to rest on at least one shot or one version with a recorded outcome.
The agent is told each added insight as a line with its number, the version it was
learned at, what it rests on and its shots, and is asked to weigh an old insight,
or one resting on a changed outcome, below the current shots. Nothing expires by
itself: **only you remove an added insight.** When the shots contradict one, the
agent proposes its **replacement** (a card showing the old text above the new, "Adding
it deletes the old insight"): your Add adds the new one and deletes the old one in
one step, and if the old one changed in the meantime the new one is added on its
own and the card says so. When one is no longer supported and nothing replaces it,
the agent proposes **deleting** it with its reason (a card with **Delete** and
**Keep**; new tool `propose_insight_deletion`, Recipe conversations only). Until you
press Delete the insight stays and is still told to every conversation. At the start
of a new version's conversation the agent is asked to look over the added insights
and propose replacing or deleting the few the shots no longer support, never as a
sweep of all of them. Removed means removed: nothing is kept as a retired state, a
history row or a restore; only the conversation that proposed the removal still shows
the text on its card and is told what you did. Deleting a conversation also deletes
the insights it proposed that you dismissed (they existed only to stop it offering
them again); waiting and added ones stay.

**Find patterns across Recipes.** A Recipe's insight reaches that Recipe alone, so a lesson that is
true across coffees ("this grinder channels below 9 clicks with any light roast") is learned
again, Recipe by Recipe. On the Knowledge page's Insights tab, **Find patterns** asks one model call to
read the confirmed insights of every Recipe (the Recipe's name and attributes, the version each was
learned at and what it rests on, the general insights that already exist, and the proposals you
declined before) and to propose general insights that several Recipes say in different words. It is
not a conversation and has no tools. It runs **only when you press the button**: never on a
timer, after a sync, at boot, or from a chat or MCP tool; the page says how many confirmed Recipe
insights arrived since the last run and keeps the button off until two Recipes have one. Each
proposal is a card with its text, the Recipes it applies to, and the Recipe insights it was derived
from, grouped by Recipe and linked. A proposal that rests on one Recipe, names an insight it was not
given, or states a scope that every source Recipe does not share (or one by profile style) is dropped
before you see it and counted on the run. **Approve** writes the general insight (confirmed, scoped
so that it reaches every one of those Recipes) and **deletes the Recipe insights it came from** in the
same step, so each of those Recipes' conversations is told the lesson once, as a general one; when
the proposal says it replaces an existing general insight, that one is deleted too. A source that
was deleted or taken back since the run is left alone and named on the card, and the approval is
refused when fewer than two Recipes' insights remain. **Dismiss** keeps the proposal so that the next run is told you declined it. Copies of insight text
live one run: when a run finishes, every proposal of every earlier run is deleted (whatever
became of it) and every earlier run's stored input is blanked, so once you delete an insight
its words can survive in the newest run's input and proposals until the next run finishes, never
longer (and while fewer than two Recipes have a confirmed insight no run can start, so they stay until
one can). Approve and Dismiss wait while a run is going: it replaces the proposals when it
finishes, so an answer given meanwhile would be lost. The model is `modelPatterns` under
Settings → LLM → Models, falling back to the default model.

**A proposal stops waiting the moment you change the Recipe another way.** Record a
version on the form, go back to a version, make a profile proposal active for the Recipe — whichever it
is, a change that was argued against the
version you have just left is retired unanswered rather than sitting there with
an Accept button that could only refuse. The log says so, and the next
conversation is told, so the agent can propose afresh against what you are
brewing now. **Accept and Decline are yours**: there is no tool, in the chat or
over MCP, that reaches either, and a test walks the registry and the tool
package's own bytecode to keep it that way.

A profile change is a change to the version too — the temperature and the
pressure curve are as much of it as the grind — so a draft proposed inside a
Recipe's conversation carries a prediction and obeys the same rules. It still lands
on the Profiles page as a proposal inside its profile: you read the diff and make it active
(one click, which approves it), and the Recipe records the new version, with the prediction, in
the same step.

A conversation in **General** is the other way round: the whole archive,
read-only. It runs SQL over the curated views, compares shots across Recipes and
drafts a profile, and sends a bean with no Recipe yet to New Recipe's design path — and it
cannot change a Recipe, because a change to a Recipe is an argument that belongs in
that Recipe's own room, where the ledger and the evidence are in front of the
model. It will tell you which Recipe's badge to open.

**One conversation per change.** New in a Recipe's list starts a fresh one on the
Recipe's current version and it stays on that version afterwards, so the list reads
as a history of what was argued rather than a pile of rooms all claiming to be
about today's version. Rows are labelled `v6`, and a version the Recipe later went
back past is muted and says *dead end*. **Discuss in chat** on a Recipe, the
**Chat** link on every entry in the experiment log, and the Recipe's button in the
**Chat about** bar above the shots table open or continue that version's
conversation with the question already typed — press any of them twice and you
land in the same room. **Accept** on a card in the conversation also tells
the agent, as your next message ("Accepted: your proposed change (Grind 2 → 1)
is now version 2 of this Recipe"), and it
answers by sending you to a new conversation for the new version: the one you
are in stays with the version it was opened on. **Decline** tells it too, with
your reason ("Declined: the dose is not the problem", or "Declined: no reason
given."), and it answers in the same conversation without sending that card
again. Answering on the Recipe page tells no conversation.

**The agent is handed the experiment before it says a word:** the Recipe and its
numbers, every version with what changed, what was predicted, against which
version and how it turned out, the track record, how much this Recipe's shots vary
when nothing changed, the evidence table this version's prediction is graded on,
this version's newest shots (twenty by default, `chatRecentShots`) with the
discards marked, the Good shots that are the target, and the insights you have
confirmed. So the first turn is about the coffee rather than about learning what
Recipe 3 is. Beside the composer, the page lists exactly what the agent can do in
*this* conversation.

**Every shot is told in two tiers, and every field is explained.** The
*base* information of a shot — what it is and where it is filed, its outcome,
the headline diagnostics and your judgement — is what the agent sees for every
shot in the opening context and in its search; the *extended* information — the
temperature, pressure and flow statistics, profile compliance, one line per phase
(how it ended, the cup at its end and its share of the target, the flows, the
water, the pressure) and the curve — is what it asks for, one shot at a time.
The base information starts with the shot's **checks**: the **warnings**, the few things that
are plainly wrong with a shot without knowing what its profile is for: over
target (the final weight above 110 % of the target yield of the version it is
filed under), under target (below 90 %), a phase of its profile skipped because
the shot stopped on weight, and fast flow (the cup flow over 3 g/s for a
second at 80 % of the peak pressure; a turbo profile does it on purpose). They
are worked out when a shot is read, so refiling a shot changes them, and each is
a fact to weigh against what the profile is for, never a verdict, unless the
profile says what it is for: an agent proposes a **signature** for a profile
version (expectations per phase, critical, important or context); it is in force at
once, unless you reject it, and every shot is checked against it from then on, so the cup being full
before the decline reads `ramp: early yield` in red and a turbo's fast flow reads
grey, as expected. You can reject any expectation (and restore it later), and a
signature carried to a new version of the profile stays in force. There is no
score and no grade on any number. The curve comes as about sixty rows
(`chatCurvePoints`) chosen to keep its shape, and it always keeps the moments
the diagnostics are about: each phase's start and end, peak pressure, first
drip and the largest pressure drop. A value the machine did not
record (no scale, no pressure sensor) is left out, never shown as zero. The
agent's instructions carry a glossary of the base fields, and the meanings of the
extended fields arrive with a shot read in detail (`get_shot_extended`,
`get_shot_full` or `compare_shots`), once in what the model is sent: on the newest
such result of the conversation, or on the answer's first when it has none yet. They
say what each measures, its
unit and which way is better; none carries a grade.

**Which item sits in which tier is yours to choose**, under **Settings → Shot
information**: one table per group, each item with what it means, a base |
extended | excluded control and its value on your newest judged shot, written by
the same renderer the agent reads. An excluded item is left out of the opening
context, the shot tools, the search and the glossary; a General chat's SQL tool
can still read the archive's views. Shot id, Recipe version and whether a shot counts stay in base, since
the agent cannot search or cite without them. The page shows the cost in
approximate tokens (base and extended per shot, the curve at `chatCurvePoints`,
the glossary in its two halves, and the opening context's shots at `chatRecentShots`), a change
applies from the next turn, and
**Reset to defaults** puts every item back. Only the items you moved are stored,
so an item a later release adds arrives at its default.

**Seventeen tools in a Recipe's conversation** — twelve reads and five that propose:
how this conversation's version turned out (`propose_outcome`), waiting for you
to accept, record another outcome or dismiss; one change to this Recipe, waiting
for you, with the prediction that makes it gradable (the grind, the dose, the
yield; a profile change is a separate card, drafted for the Recipe's own profile); an insight about this Recipe stored **unconfirmed** that reaches no
future prompt until you add it (naming the shots and versions it rests on, and the added
insight it replaces, if any); the deletion of one added insight, waiting for you to
delete or keep it; and a
profile draft that goes through the same schema, safety-policy and clamp checks
as one typed by hand. A Recipe conversation starts with the profile its version brews (and the
compared version's, when it is another), as stored; `get_profile` reads any
other profile version's whole document, and three
read one shot: `get_shot` its base information, `get_shot_extended` the rest and
`get_shot_full` both, beside `list_recipe_shots`, which searches the Recipe's shots on
their base information (filters, ranges, a sort, at most ten back).
**Seventeen in General** — sixteen reads and one proposal, the profile draft. A
new bean is not worked out there: the General chat sends you to New Recipe →
**Design it with the agent**, whose conversation ends in a first version you
accept. **Eight while a Recipe is being designed** (below). The registry holds
twenty-three in total: twelve both kinds have, ten that belong to one kind or the
other, and `propose_initial_recipe`, which only a design has. None of them
starts a shot's review or answers one of its claims: only a person's click does. Nothing in the
chat can touch
the machine — making a profile active stays a button you press.

**A Recipe can be designed in its own conversation.** `POST /api/recipes/design`
takes a bean and a grinder (both required), optionally a profile to fork, your
usual grind on that grinder and what you want from the coffee, and creates a
Recipe whose version 1 has no numbers yet, with that version's conversation open.
While the Recipe is being designed the agent is told what you asked for, the
profile to fork in full, how this bean went in your other Recipes, similar Recipes on
this grinder and the matching rules; it asks what it needs and then proposes
**the first version** with `propose_initial_recipe`: a profile of its own
(a Recipe has one profile, and no other Recipe brews it) plus grind, dose and yield, as one card you accept or
decline. The profile starts from the one you picked to fork, or, when you
picked none, from nothing: the agent writes it whole, and never builds on a
profile of yours you did not choose. A newer card replaces the waiting one. Accepting fills
version 1 in place and ends the design conversation: the agent is told, and
tells you to talk version 1's shots through in a new conversation (Discuss in chat on
the Recipe page opens one rather than the design); its profile is approved with the card, so the
card's **Accept and sync** is the whole way to the machine. Writing a version by hand, which must name a profile no Recipe has, ends the design
the same way. A design nobody brewed anything under can be discarded
(`DELETE /api/recipes/{id}/design`).

Every answer shows what was called, with the input and the output one click
away, and citations are links: a shot id goes to the shot, a knowledge passage's
heading path goes to the passage. A turn is bounded by `chatMaxToolRounds` and
`chatMaxToolCalls`; the Stop button cancels a run mid-answer, and the transcript
survives a reload because the stream is replayed from the database rather than
held in the tab.

## A starting point for a new coffee

**New Recipe** is one form: a name, the bean, the grinder, the profile, the
numbers and an optional intent. The name starts empty and fills itself in as
"<bean> (<grinder>)" once both are picked, following a later pick until
you type your own; left empty with no grinder, the Recipe is called after the
bean. The profile is one no Recipe has, by the version it has active; while
Writes are off the list holds none, and the machine's own files stand in (the Recipe has its
profile once the list adopts the file). Profiles that already have a Recipe are listed after them,
each with its Recipe's name, under "Has a Recipe: picking one duplicates it": picking one opens
**Duplicate Profile** on its active version, and saving the duplicate adds it and selects it in
the picker (Cancel leaves the picker as it was). Picking a profile fills the target yield from its
largest volumetric stop, never over a number you typed, and shows the
temperature it brews at beside the numbers — that one is the profile's to state,
not yours to type.

Open a coffee nobody has brewed and **Suggest a starting point instead**, folded
under that form, answers the question you actually have. It reads the bean and
grinder already picked and shows what this archive has already brewed on
*this grinder* that resembles it — same roast level, same process, same
origin — with how each one went: shots, mean rating, ratio
and time. That half is one SQL query, costs nothing, and is worth reading on its
own. A version with no shots behind it is never offered: it records an intention,
not a result.

Press **Ask for suggestions** and the model turns that plus the rule tier into
three complete first versions — conservative, recommended, adventurous — each
with a grind, a dose, a yield, a temperature, a profile and a rationale citing
what it leaned on.

An option's temperature has to be true once you take it, and only a profile can
make it so. When an option points at a profile you already have and suggests a
temperature that profile does not brew at, the card says that taking it will
**make a draft** of that profile at the suggested temperature, and the new
Recipe's first version points at the draft. Nothing is sent to the machine: you
make it active from the Profiles page, exactly as you would any other
proposal, and the next sync puts it on the machine.

It will not invent a grind number. A grinder's scale is arbitrary and there is
no conversion between two of them, so a figure on your dial is offered only when
your usual setting or a past Recipe on the same grinder anchors it; otherwise the
answer is relative and the card says so.

Taking one creates the Recipe with `origin=starting_point`, and — when the option
authored a whole profile rather than pointing at one you already have — a proposal
waiting on the **Profiles** page. Nothing is pushed; you make it active. The Beans
page has the same shortcut for the coffee you are looking at. The chat does not
ask for starting points: a run it started had nowhere to be taken, and
designing a Recipe with the agent (below) is the conversation for a new bean.

## Designing a Recipe with the agent

The third path in **New Recipe** is **Design it with the agent**, folded under the
form like the suggestion. It is for a new profile rather than a first guess: a
fork of one you have, or a bean you already brew in another Recipe. It uses the
name, bean and grinder from the form, reads the form's profile as the one to
fork from, and asks only what the form does not: your usual grind on that
grinder and what you want from the coffee. The grinder is required here — a
grind is only a number on one grinder's dial — so pre-ground coffee keeps the
plain form and the suggestion. Whichever of the form's grind, dose and yield
you filled in goes with it, as your decision: the agent proposes exactly those
numbers, designs the profile around them (a yield you chose is where its last
phase stops) and works out only what you left empty, changing one of yours
only when you ask in the conversation. A yield the picked profile filled in
counts as filled; clear it to leave it to the agent. The intent is not sent:
the design asks what you want from the coffee instead. The Recipe page shows the
numbers it keeps under the brief.

**Start designing** creates the Recipe with an empty version 1 and takes you to
that version's conversation, with what you wanted as the first message (or
"Help me design this Recipe." when you left it empty, so the agent starts by
asking). The agent already has your brief, the profile to fork, this bean's
other Recipes and similar Recipes on this grinder; it asks what it needs, then
proposes the whole first version as one card — a profile of its own, the grind
(said to be relative when nothing anchors a number on your dial), the dose and
the yield. Nothing exists until you accept it, in the conversation or on the
Recipe page. Accepting makes it version 1 and ends the design: the agent tells you
to start a new conversation about the shots, since one conversation is one
version. Accepting also approves the card's profile, under the Name you typed on the card, and
switches it on: **Accept and sync** with Writes on starts the sync that puts it on the machine,
and with Writes off **Accept** leaves it for the first sync after you turn Writes on. Once it is
on the machine and you have selected it there, shots brewed on it are filed under the new Recipe.

Until then the Recipe carries a **Designing** badge on the Recipes list, on its page
and on its badge on the Chat page, with **Continue designing** back into the
conversation. Recording a version by hand on the Recipe page ends the design the
same way. **Discard design** on the Recipe page deletes a design nobody brewed
anything under, after asking; one with a shot filed on it is kept.

The conversation needs an LLM provider (Settings → LLM); everything up to it,
and the card, work without one.

## The chat's database tool (MCP)

The `claude_code` provider runs the chat's tool loop inside the Claude Code CLI,
and the CLI calls tools only through an MCP server. So for each chat turn it
starts `gaggiclanker mcp` as a child process over stdio, pointed at the same
`DATA_DIR` and told which conversation it is serving, and the model gets exactly
the tools the chat has with any other provider — the Recipe's tools inside a Recipe's
folder, the archive's in General. That server is internal to the chat: it opens the database and nothing
else — no network endpoint, no machine connection, no setting — and like every
tool it only reads the archive or proposes something a person answers (a signature is in
force until they reject it). The API
providers call the same tools directly and never start it.

The command stays available so the provider can spawn it, and it deliberately
never creates the database, because a second process creating the schema of a database the
application is also using is a race: start the server once first. There are no
device-write tools and no switch that adds any: the machine is written only by
gaggiclanker's own HTTP API, behind buttons a person presses.
