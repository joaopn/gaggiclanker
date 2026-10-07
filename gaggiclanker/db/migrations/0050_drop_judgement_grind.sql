-- A judgement no longer records a grind setting.
--
-- Breaking, on purpose: the column and whatever people typed into it are dropped. The grind a shot
-- was brewed at is the Set version's recipe (`recipe_grind`) or the machine's own note
-- (`note_grind`, kept on the notes card); a third copy typed into the judgement form only ever
-- disagreed with those two. The shot-information item `grind_as_brewed` goes with it, and so does
-- the tier a person may have chosen for it.
--
-- `v_judgements` names the column, and SQLite will not drop a column a view uses, so the view is
-- dropped first and made again without it.

DROP VIEW v_judgements;

ALTER TABLE shot_judgements DROP COLUMN grind_setting;

CREATE VIEW v_judgements AS
SELECT j.shot_id,
       j.rating,
       j.balance,
       j.taste_notes_json,
       j.aroma_notes_json,
       j.dose_in_g,
       j.dose_out_g,
       j.decision,
       j.notes,
       j.updated_at,
       s.started_at,
       s.set_version_id
  FROM shot_judgements j
  JOIN shots s ON s.id = j.shot_id;

DELETE FROM shot_info_tiers WHERE item_key = 'grind_as_brewed';
