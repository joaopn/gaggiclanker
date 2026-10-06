-- The shot-information item `warnings` is now `checks` (the Checks group replaced the Warnings
-- group: the signature's state, what failed, and the universal warnings among them), so a tier
-- a person chose for it follows it. Nothing else changes.

UPDATE shot_info_tiers SET item_key = 'checks' WHERE item_key = 'warnings';
