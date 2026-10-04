-- A draft no longer records a stop-condition acknowledgement.
--
-- Making a draft active used to be refused until a person ticked "I know this changes when the
-- machine stops", and the tick was stored on the draft. The check is gone (the card still shows
-- the stop-condition change, and the agent is still told never to make one unprompted), so the
-- column that held the tick has nothing to say. It sits in no index, view, trigger or CHECK, so
-- it is dropped in place.
--
-- The values lost are a yes/no that nothing reads any more. No transaction control in this file:
-- the runner wraps it plus its ledger row in one.

ALTER TABLE profile_drafts DROP COLUMN acknowledged_stop_changes;
