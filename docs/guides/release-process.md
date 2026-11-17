# Release workflow / 发布流程

This guide describes the repository's release automation. Changes to these
helpers do not select a new version or authorize a tag, push or publication.
Release helpers live in the checkout; they are not npm CLI entrypoints.

## Before publishing

Review the public diff, package scope and release notes. Choose the exact
version and obtain the operator's release authorization. The package version
and tag must agree. Check development CI for the exact source commit.

Manual dispatch of the Release workflow only validates the existing npm
credential and package identity. It does not publish. An authorized project
tag starts the release job: stable tags use npm latest; release candidates
use rc and hotfix tags use hotfix. RC and hotfix use GitHub prereleases.

The job renders the version's source notes into a separate temporary file.
Local Markdown link destinations become fixed-tag GitHub URLs; targets must
exist in the checkout and be tracked by Git. External URLs, fragment-only
links and code examples remain unchanged. Inline images use raw URLs.
The helper supports inline and reference Markdown links; use angle-wrapped
destinations for paths containing spaces or parentheses. Source notes are
kept as repository documentation, with their relative links intact.

The pinned MCP SDK coverage gate and full tests run before packing. The job
packs once, records SHA256, attaches that archive and checksum to GitHub,
then publishes the same archive to npm with provenance. An existing npm
version skips duplicate publication but still requires independent checks.

## Independent publication check

The final read-only helper checks:

- the exact npm package/version and intended dist-tag;
- npm SHA1/SHA512 metadata and downloaded bytes against the local archive;
- the GitHub tag's peeled commit and stable/prerelease classification;
- Release text against the separately rendered notes;
- both GitHub assets, their reported digests and the checksum file;
- identical local, GitHub and npm archive bytes.

It uses public registry reads and the existing GitHub CLI authentication.
It never invokes publish, changes a dist-tag, edits a Release or mutates Git.
Only the intended npm channel is checked; this helper does not certify that
other channels or older package versions remained unchanged.

The default polling deadline is 15 minutes with 15-second intervals and
individual read timeouts. The Actions step also has a 20-minute outer limit.
Unready metadata/assets and read failures retain separate observations.
Content mismatches stop immediately. Polling retries reads only; it never
retries publication. A timeout leaves the existing release in place and
fails the verification step for inspection.

| Result | Exit | Meaning |
|---|---|---|
| PASS | 0 | Every listed publication check passed |
| MISMATCH | 1 | Observable identity, notes, digest or bytes differ |
| NOT_READY | 2 | Required version, channel or assets were still unavailable at the deadline |
| READ_ERROR | 3 | The last observation was a transport, API or metadata-read error at the deadline |
| INPUT_ERROR | 4 | Local archive, identity, notes or other required input is invalid |

Argument errors exit 2 before polling. Each attempt has its own metadata and
result files. report.json and summary.txt record the final observation and
completed checks. The fixed summary is appended to the Actions step summary;
available evidence and rendered notes are uploaded even after a failed step.
Raw authenticated configuration and temporary signed download URLs are not
copied into error summaries.

This check does not install the package, run model/MCP tasks or verify registry
signatures and provenance attestations cryptographically. The existing release
test gate covers its own checkout/runtime scope. Separate installation or
signature audits retain their own results; npm documents the signature and
attestation check in [Viewing package provenance](https://docs.npmjs.com/viewing-package-provenance/).

## Rechecking an existing release

Use an already accepted archive from that release, its exact commit and a
fresh output directory. A development archive with newer documentation is
not the published archive even when package.json still has the same version.

```sh
python3 .github/scripts/release_notes.py --repository owner/repository --version '<version>' --output /path/to/rendered-notes.md
python3 .github/scripts/verify_release.py --repository owner/repository --version '<version>' --commit '<full-commit-sha>' --tarball /path/to/accepted-release.tgz --notes /path/to/rendered-notes.md --output-dir /path/to/new-evidence-directory
```

If the check fails, inspect its status and retained observations. Recheck with
a fresh directory after resolving the specific issue. Passing a later read
does not erase the earlier failure or establish its root cause. A failed
check does not authorize replacing a published package or moving its tag.
