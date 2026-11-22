---
title: Profiles and the machine
---
# Profiles and the machine

## What gaggiclanker writes to the machine, and who starts it

Nothing, until the **Writes** switch in the top bar, beside the machine status, is on. It
is off by default; turning it on asks first, turning it off is immediate. With it
on, **the only thing this box ever writes to the machine is a profile** (see
below). It never deletes a shot from the machine, never writes a judgement to a
shot's notes card and never changes a device setting.

While the switch is on, a **Flush** button sits beside it: one click runs the machine's
own flush for the duration set on the machine, as the Flush button on the machine's web
UI does. It works in brew mode with nothing running, and is not recorded anywhere.
Next to Sync, a **Switch to Brew** button (it reads **Switch to Standby** while the
machine is in brew mode) does what the mode buttons on the machine's web UI do. It is
there only while the switch is on, is refused while a shot or a flush is running (the
machine would stop it) and is not recorded either.

Nothing an agent does is on that list. A proposed change to a Recipe is a row in
this archive waiting for you, and accepting it records a version — it sends
nothing. A profile a conversation proposes is a proposal card in that conversation (and inside
its profile on the Profiles page), which you approve yourself: **Approve and sync** with Writes
on, **Approve** with Writes off. A profile that has a Recipe is that Recipe's profile: approving a
change to it makes it the profile's active version and records it as the Recipe's next version
in the same step (with the prediction, when it was proposed in the Recipe's conversation).

The machine is a buffer, not an archive: when its storage runs low its own
firmware deletes its oldest shots, whether or not this box has them. That is
accepted rather than managed from here, so sync often enough
that nothing waits on the machine for long. The machine's notes cards are read
on a sync too, and never written back.

Nothing runs on its own: a sync is something you ask for, and there is no timer. With
the Writes switch on, the one thing a sync writes is the profile list's write phase at its
end (see below); nothing else, and no tool a model calls, starts a write. Every write
attempt, refused ones included, is listed under **Recent writes** on the Device page; a
flush or a mode switch, which store nothing, is not.

## The profile list

With the **Writes** switch on, every sync ends by making the machine hold exactly the
profiles that are **on the machine** in the list on the Profiles page. The first sync with the
switch on takes the machine's profiles into the list as they are (the home screen is each
profile's star) and writes nothing; the switch's confirmation says so before you turn it on,
and after that shows what the next sync would do, read from the machine at that moment. From
then on a sync saves each profile's active version over the profile's own file on the machine
(a profile keeps one file, one id, for good, and its star and selection stay where they are; a
profile with no file gets one), **removes the
ones switched off (the firmware's own profiles and ones you made on the display included, but
not the backflush and descale that firmware 1.10 keeps for itself)**,
and sets the stars (a profile's **Starred** flag, applied only while it is on the machine, at
most ten: a further one is left unstarred and the sync says so). A
file the app has never seen joins the list, on, and is never touched by the sync that finds
it. A profile changed on the display is never overwritten silently: it becomes a **conflict**,
nothing is done for it, and you choose which side to keep (**Keep the app's**: the next sync
saves the app's version over the machine's file; **Keep the machine's**: its content becomes the profile's active
version; either way it is stored as a version). Every removal is guarded by a fresh load that
must hold exactly what was last recorded. If the machine looks reset (none of the files the
last sync left is on it), syncs stop writing and the Profiles page asks once whether to put the
profiles back, with one button.

**No two profiles in the list share a name.** A proposal that would make a second profile with
a name the list already has is refused (its panel says the list already has that profile, and
offers Decline), and a file on the machine with a name the list already has joins as
"<name> (2)" (the next sync renames the file on the machine). A profile renamed on the machine
takes the machine's new name at the next sync, with Writes on or off; one renamed in the app has
its file renamed at the next sync with Writes on; renamed on both sides, it is a conflict.

## Putting a profile on the machine

A profile reaches the machine only by being on in the list with an active version.

**Duplicate Profile** on a version makes a new profile from it. The dialog is titled with the
original's name; its **Name** field starts empty and must be filled, and it is the one place the
name is set (the JSON below it has no `label`). Edit the JSON first if you like: it is validated
as you type against the strict schema and the safety policy, and a number the policy would move
blocks the save, since a duplicate must brew what it shows. **Save** adds the profile to the list
switched on, so the next sync with Writes on puts it on the machine; there is no proposal to
approve. A duplicate is always a profile of its own: a name that is already a profile, the
original's included, is refused under the field.

A change to an existing profile starts as a proposal, from the chat or the starting-point wizard.
A change that keeps a profile's name is a new version of that profile, whoever made it and
whichever version it started from: the machine's own profiles and ones made on its display
included, under their exact name. A changed name, or a profile written from scratch, is a profile
of its own, stored under exactly the name it was given (the app adds nothing to it), and a name
that is already a profile is refused.
It waits as a **proposal card**, in the conversation where the agent proposed it and inside its
profile's dropdown on the Profiles page (marked **Proposed**), or as a row marked **New** when it
is a new profile. The card is one proposed version of one profile and where it stands, from
proposed to on the machine: the change in the agent's words, its prediction for a Recipe, the
profile's curve beside the active version's on the same axes (pro profiles; a new profile shows
one), what changes (a new profile is shown whole), the stop-condition warning when it moves when
the machine stops pumping, and for a new profile a **Name** field, prefilled with the agent's
name and checked against the list as you type. **Approve and sync** is one click: it approves the
proposal (the profile's active version, switched on, and for a proposal made for a Recipe the Recipe's
next version, with the **Major change** box, once the sync has put the profile on the machine)
and then starts the same sync as the top bar's button; with Writes off it is **Approve**, and
the profile goes to the machine at the first sync after you turn Writes on. It does not select
the profile on the machine: you still do that. After the click the card goes on telling the
truth in the server's words: syncing, on the machine (and whether it is the selected profile),
not on the machine and why (with a link to the profile), declined or replaced. In the
conversation the answer is also told to the agent ("Approved: Adaptive Bloom as v1.2 of Guji
natural.", "Declined: <your note>"). Any older version can be made active from the dropdown;
there is no separate
going back, and no Delete: switching a profile off removes it from the machine at the next sync,
and the profile and its versions stay in the list.

The next sync with the **Writes** switch in the top bar on saves the active version as a new
profile under its name, never over an existing file. The machine is
read again first: a profile already holding the same content is reused, and a new version of a
profile replaces this app's previous copy, carrying its star and selection, instead of piling
up versions. A copy you edited on the display since is never deleted unseen: it is a conflict
you settle. What came back off the machine is compared against what was sent, and a mismatch
removes the copy just written and keeps the previous version. `docs/safety-layers.md` is the
whole contract.

Profile exports can be uploaded straight into the library with **Upload
profile** in the Profiles header — it runs them through the same importer as the
shots page, and the profile appears in the list switched off (or, when a profile already has
its name, as one of its versions), so importing never changes what a sync does.
