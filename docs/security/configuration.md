# User configuration and output provisioning

`PassiveListener.exe init-config C:\Users\USER\AppData\Local\PassiveListener`
creates a private leaf directory and a flushed, atomically published settings.json.
The parent must already exist. Run as the capture user, never SYSTEM/LocalService.
Existing settings are never replaced: repeated initialization accepts only identical
validated settings. To change settings, stop capture, edit the private settings.json,
then run `validate-config` with the same directory. Configuration reload is startup-only.

`PassiveListener.exe provision-output <configuration-directory>` validates settings
and creates missing output ancestors, including C:\temp when absent, with private
user/SYSTEM ACLs from creation. Existing ancestors remain unchanged and leased;
the output leaf must satisfy the exact private policy. An existing public output
leaf is rejected, never silently repaired or adopted. No transcript is written by
these maintenance commands. Directory provisioning is idempotent, not an all-or-
nothing transaction: failure can leave empty private ancestors for retry.

Schema version for this candidate is the field set in config/settings.schema.json.
Missing fields use documented schema defaults. Unknown and duplicate fields, booleans
as numbers, non-finite numbers, unsupported models/languages, and noncanonical paths
are rejected. Config reads are bounded to 16 KiB and use a retained exclusive regular
file handle; hard links/reparse files and untrusted config directory ACLs fail closed.
No path environment expansion, network/model download, external prompt routing,
TTS, or wake-word activation is supported. Microphone null means default input;
integer IDs are selected by the future capture adapter. These commands do not prove
that an input device or model exists.

Failed settings writes retain .settings-*.partial and never promote them on restart.
They contain settings only, but remain private. Inspect offline; no automated cleanup.
Flush/fsync plus Windows non-replacing rename does not establish power-loss durability.
Native capture, loader lifetime, service/task/installer and runtime integration remain
separate candidate requirements. No microphone or reboot measurement is claimed.
