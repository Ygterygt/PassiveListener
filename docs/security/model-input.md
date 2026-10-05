# Windows model input boundary

`verified_input(path, sha256, size)` opens local fixed-drive ancestors in order,
then the file, using `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT`. Metadata
comes from each held handle. Reparse points, unexpected types and multiple hard
links are rejected. Write and delete sharing are withheld throughout the context.
SHA-256 and length verification read the same handle delivered to the consumer,
rewound to zero. Exceptions release the handles. Errors omit paths and content.

Keep the context alive through the entire consumer read/mapping lifetime. Never
return its pathname to a later loader after closing it. The CLI now exercises the
lease, but a successful CLI check does not secure a subsequent independent load.
The older `verify_file` helper remains a point-in-time diagnostic, not a loader.

Windows API source: [CreateFileW sharing and reparse semantics](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).

This boundary assumes the OS and same-process code are trusted. It does not defend
against privileged volume access or hostile code executing within the worker.
No permissions, privileges or host policy are changed. Unsupported paths and
sharing conflicts fail closed; there is no permissive fallback. Directory sharing
may conflict with maintenance tools; retry only after the conflicting activity ends.

Production integration remains incomplete: installer-owned ACLs, trusted manifest
selection, protected staging, native loader compatibility and lifecycle tests must
be implemented before claiming production model integrity. Caller-supplied hashes
are not an authenticity mechanism. No STT engine or model is bundled here.

The Windows unit suite uses fixed synthetic bytes. It executes write/replacement
and ancestor rename attempts while the lease is open, rejects an already-open
writer, rejects hard links and actual directory junctions, and verifies cleanup
on bad hashes and consumer exceptions. These are filesystem tests, not microphone,
native engine loading, reboot or performance acceptance.
