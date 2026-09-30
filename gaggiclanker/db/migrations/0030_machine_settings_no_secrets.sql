-- The machine's `GET /api/settings` document was stored whole, and firmware
-- v1.9.0 puts its Wi-Fi, access-point and Home Assistant passwords in it. The
-- identity pass now removes secrets before it stores anything; this removes
-- what earlier passes already stored, so the archive is clean on the next boot.
--
-- SQL cannot match "any key whose name contains password, token or secret" at
-- any depth, so this removes the three keys the firmware sends, at the top level
-- where it puts them. Anything else the Python filter would drop goes on the next
-- connect, when the identity pass rewrites both columns.
UPDATE machines
   SET settings_json = json_remove(settings_json, '$.wifiPassword', '$.apPassword', '$.haPassword')
 WHERE settings_json IS NOT NULL AND json_valid(settings_json);

UPDATE machines
   SET identity_json = json_remove(identity_json, '$.wifiPassword', '$.apPassword', '$.haPassword')
 WHERE identity_json IS NOT NULL AND json_valid(identity_json);
