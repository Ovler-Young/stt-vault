# Release Tag Trust

`release-tag-signers.v1.json` is the public-only allowlist for signed
`mod-whisper-cpu` release tags. No active signer is committed yet, so the
release workflow intentionally fails before registry authentication or image
staging.

To onboard a signer, commit its ASCII-armored public key under
`release-tag-keys/` and add one active entry whose `public_key` is that
filename, `status` is `active`, with the key fingerprint and its UTC `not_before` and `not_after`
timestamps. The workflow imports only these
repository public keys into a temporary `GNUPGHOME`, requires an annotated tag,
uses `git verify-tag`, rejects GnuPG expiration or revocation status, and
matches the verified fingerprint to the active allowlist.

Rotate keys by adding the successor public key and active entry before the
prior key expires. Then move the prior entry to `retired_signers` with its
revocation or expiration timestamp. Private signing keys, passwords, and
signer secrets must never be committed here. Tag-signing trust is independent
from the Cosign public key used to verify staged image signatures.
