# Hosted Edge publication and acceptance

This runbook is an operator gate. Repository code can build and verify an Edge,
but it does not change package visibility, create a hosting account, accept a
paid plan, deploy a service, or handle real provider credentials automatically.

The committed `render.yaml` is deliberately inert: its sole image URL is a
placeholder. The image workflow emits a replacement that contains the exact
`ghcr.io/...@sha256:...` reference it published. Render must pull that reviewed
image; it must not rebuild an Edge from the repository or consume `latest`.

## 1. Publish one frozen source commit

First record a full commit that passed the local and hosted test gates. On
PowerShell:

```powershell
$sourceCommit = git rev-parse HEAD
gh workflow run edge-image.yml --field operation=publish --field source_commit=$sourceCommit
gh run list --workflow edge-image.yml --limit 1
```

On macOS or Linux:

```sh
source_commit=$(git rev-parse HEAD)
gh workflow run edge-image.yml -f operation=publish -f source_commit="$source_commit"
gh run list --workflow edge-image.yml --limit 1
```

The job first starts the built container as UID 10001, verifies that only health
and one-time claim routes work before claim, and then publishes a single
`linux/amd64` image with BuildKit provenance and an SBOM. It produces an
`edge-handoff-<commit>` workflow artifact containing:

- `edge-image.json`, the deterministic source/digest/platform record;
- `render.yaml`, pinned to that exact digest.

Download the handoff with `gh run download RUN_ID --name
edge-handoff-COMMIT --dir dist/edge-handoff`. Compare the commit and digest with
the job summary and GitHub attestation. These files contain no claim or runtime
credentials.

## 2. Make the package public and prove anonymous access

New GHCR packages can remain private even when their source repository is
public. In the GitHub package settings for `all-the-context-edge`, explicitly
change package visibility to **Public**. Do not add a registry credential to the
anonymous verification job.

Run the second workflow operation with the exact reference from
`edge-image.json`:

```powershell
gh workflow run edge-image.yml --field operation=verify-public --field source_commit=$sourceCommit --field image_reference='ghcr.io/OWNER/all-the-context-edge@sha256:DIGEST'
```

```sh
gh workflow run edge-image.yml -f operation=verify-public -f source_commit="$source_commit" -f image_reference='ghcr.io/OWNER/all-the-context-edge@sha256:DIGEST'
```

That fresh job requests an anonymous GHCR bearer token, hashes the returned
manifest, performs a Docker pull without a login step, checks the embedded source
revision, and verifies GitHub provenance. A private package or mismatched digest
fails closed.

## 3. Commit and activate the reviewed deploy handoff

The image source commit necessarily precedes the commit that can name its
resulting digest. Activation therefore uses two explicit repository commits and
a one-use deployment branch:

1. After anonymous verification, review and copy the generated `render.yaml`
   into the repository root. Run the project gate and commit it. Record this
   Blueprint commit separately from the earlier image source commit.
2. Read `deployment_branch` from `edge-image.json`. Its value is
   `edge-deploy-<full 64-character digest hex>`. Create that branch at the Blueprint
   commit and push it explicitly. Never move or reuse this branch.
3. Run the activation tool from the exact Blueprint commit. It reads the image
   metadata, verifies committed `render.yaml`, and uses `git ls-remote` to prove
   that the public versioned branch resolves to the same commit before writing
   the packaged defaults:

```powershell
$blueprintCommit = git rev-parse HEAD
$deployBranch = (Get-Content .\dist\edge-handoff\edge-image.json | ConvertFrom-Json).deployment_branch
git branch $deployBranch $blueprintCommit
git push origin "refs/heads/${deployBranch}:refs/heads/${deployBranch}"
python scripts\activate_edge_deployment.py --metadata .\dist\edge-handoff\edge-image.json --blueprint-commit $blueprintCommit
```

```sh
blueprint_commit=$(git rev-parse HEAD)
deploy_branch=$(python -c 'import json; print(json.load(open("dist/edge-handoff/edge-image.json", encoding="utf-8"))["deployment_branch"])')
git branch "$deploy_branch" "$blueprint_commit"
git push origin "refs/heads/$deploy_branch:refs/heads/$deploy_branch"
python scripts/activate_edge_deployment.py --metadata ./dist/edge-handoff/edge-image.json --blueprint-commit "$blueprint_commit"
```

The tool never creates or pushes the branch. If the remote branch is missing,
moved, or contains the placeholder, activation fails without enabling the
button. Review the generated `edge_deployment_defaults.py`, run the full gate,
and commit it as the later Core packaging commit.

Render documents `/tree/branch_name`, not arbitrary commit-SHA URLs, so ATC uses
the unique digest-derived branch and records its resolved Blueprint commit in
the package. Whether Render accepts and deploys the branch remains unproven
until the real-host acceptance in step 4 passes. Branch mutation after
activation is an operator/repository-control risk; do not force-push or delete a
branch referenced by a shipped Core.

The Core dashboard exposes the Render button only when deploy URL, deployment
branch, image digest, image source commit, and Blueprint commit are all present
and valid. The branch name must match the digest. A partial value, tag, zero
digest, non-HTTPS link, or unreviewed package default disables the button.

An advanced development build can use the same all-or-nothing environment
override:

- `ATC_EDGE_DEPLOY_URL`
- `ATC_EDGE_DEPLOY_BRANCH`
- `ATC_EDGE_IMAGE_REFERENCE`
- `ATC_EDGE_SOURCE_COMMIT`
- `ATC_EDGE_BLUEPRINT_COMMIT`

The deploy URL must be an HTTPS `render.com/deploy` link to a public HTTPS Git
repository's digest-derived deployment branch. The image must be the exact
lower-case GHCR digest reference.

## 4. Exercise a real host without personal context

Use a new, empty directory. The acceptance command refuses a nonempty directory
that is not already its own marked acceptance workspace.

```powershell
python scripts\accept_hosted_edge.py prepare --workspace .\tmp\hosted-edge-acceptance --output-directory .\tmp\hosted-edge-secrets
```

```sh
python scripts/accept_hosted_edge.py prepare --workspace ./tmp/hosted-edge-acceptance --output-directory ./tmp/hosted-edge-secrets
```

The command creates an isolated synthetic Core, one `always_available` test
record, `setup.env`, and a separate recovery-code file. It does not print either
secret. Keep the recovery code offline. In Render, review the Blueprint's paid
plan and persistent-disk cost, explicitly approve creation, and import
`setup.env` for `ATC_EDGE_BUNDLE`. Delete the local `setup.env` after the
provider has stored it.

Once Render reports a stable HTTPS service URL, run:

```powershell
python scripts\accept_hosted_edge.py verify --workspace .\tmp\hosted-edge-acceptance --edge-url https://YOUR-SERVICE.onrender.com
```

```sh
python scripts/accept_hosted_edge.py verify --workspace ./tmp/hosted-edge-acceptance --edge-url https://YOUR-SERVICE.onrender.com
```

Verification requires the service to be inert before claim, completes the
origin-bound public-key claim, verifies Core's pairing proof, pushes the signed
ordered event, and confirms Edge's sequence plus Core's empty outbox. The report
contains status only, never the claim, recovery code, replication credentials,
or synthetic record content.

Provider deployment remains unproven until this command passes against the real
URL. Provider OAuth, Claude/mobile retrieval, Core-online forwarding, and
decommission are separate acceptance gates. For a disposable test deployment,
decommission it through Core before deleting the Render service, disk, and any
provider backups.

References: [Render Blueprint image runtime](https://render.com/docs/blueprint-spec),
[prebuilt image digests](https://render.com/docs/deploying-an-image), and
[Deploy to Render links](https://render.com/docs/deploy-to-render).
