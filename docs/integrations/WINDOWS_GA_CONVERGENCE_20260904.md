# Windows GA convergence integration ledger — 2026-09-04

This ledger records the exact-source integration performed on branch
`hardening/windows-ga-convergence-20260904`. It is provenance, not release,
signing, clean-machine, Defender, provider/client, or downloaded-candidate
acceptance evidence.

## Exact starting state

| Check | Result |
|---|---|
| Requested base | `7bfd070fd51541cd77f3cde67576f447cdef50bd` |
| Local `refs/remotes/origin/main` before mutation | `7bfd070fd51541cd77f3cde67576f447cdef50bd` |
| Remote `git ls-remote origin refs/heads/main` before mutation | `7bfd070fd51541cd77f3cde67576f447cdef50bd` |
| Starting worktree | Clean, detached at the exact base |
| Open PR check before mutation | No open PR targeting `main` |
| Integration branch | `hardening/windows-ga-convergence-20260904` |
| Required source tips | 9 tips listed below |
| Union result | 49 unique source commits; no duplicate stable patch IDs |
| Patch-equivalence check | No source commit was already patch-equivalent on exact `origin/main` |
| Shared ancestry | Tips 1–5, 7–9 share parent `58a2410a7e92484a3fd81d0126b7d1ae6dc631e9`; tip 6 is based directly on the requested base |

The union was topologically ordered from the exact base. Every row below was
cherry-picked once. `Applied` means the source change is represented in the
integration ancestry; it does not claim that the resulting commit has the
same SHA or patch ID after conflict resolution.

## Required source tips

| Lane | Source tip | Scope |
|---|---|---|
| 1 | `e7aedd6c3c2a22bd06927cf90f9420a437e410be` | Windows bootstrap recovery |
| 2 | `bfb662588361d2e0841300cc2699652f6f32aff9` | Dogfood runtime recovery/readiness |
| 3 | `5d651cb42f1b30e24ff56108b64c0d873cfe8ae0` | Core lifecycle and upload cancellation |
| 4 | `bd858d408d56826afdd0b8f435c7cbeb093a86c0` | Runtime readiness lifecycle |
| 5 | `377c5a1c18fc50d89031ca85e5aec530f3b2a616` | Windows registration and registry cleanup |
| 6 | `de68f18d9e779d071042681df149624246350d0b` | Packaged startup/update terminal replay |
| 7 | `284e2985131589b98cd48d34f1f1706fd878e6ad` | Portable restore graph containment |
| 8 | `832779b25e1cf5ce79011cec10f5907fb21f3e4e` | Vault/target-scoped purge barriers |
| 9 | `0968a24dff48cd74c3b3301f0ad1298df6527367` | Portable authorization-state boundary |

## Topological patch ledger

| # | Source commit | Integrated commit | Subject | Result |
|---:|---|---|---|---|
| 1 | `25a59f3c3d3c8f03869f4cecc6c8ae2b51324763` | `29471c7ed6db887f1a315d8e9ee13590196d6b42` | Harden Windows bootstrap installation transaction | Applied |
| 2 | `19aedd5c0e33cc5977eae572bff8078bbce4dd01` | `b6cda1d1ead27df849eb44c7fe40f0103c2b0367` | Harden Windows bootstrap recovery cleanup | Applied |
| 3 | `b61cee660654a50d919d3bbd1291b87c411057b6` | `f2b8b8ae324b1ca390b5830dc22a7e4467ed684d` | Fix Windows bootstrap recovery cleanup retries | Applied |
| 4 | `e7aedd6c3c2a22bd06927cf90f9420a437e410be` | `89b57c50d7c138472ac6b64d3fca80db5a8537f` | Fix bootstrap directory create-raise cleanup | Applied |
| 5 | `78c2401bf08a9f901af4ca57b1de46b40d75605d` | `5e42b3225a4cb86916f23dfe799009a8fe0b2ee7` | Gate Windows update activation on Core quiescence | Applied |
| 6 | `6948803a24d581832d5976bf4a6ba590350b9cc7` | `c091b98356aca362b5b0d3fe340f0c055500834c` | Harden Core quiescence and shutdown lifecycles | Applied |
| 7 | `b5c553ecdd8f85cbd29291e3fad5ceeb10981fcf` | `8c1560c1eca7c726139cfced871460591bdddd06` | Close multipart import activity admission gap | Applied |
| 8 | `7ba0866e990ded035c690ddc2b9029d4cbe52e9e` | `fe360408cbf8148f990cd44d4a8cdac9fb0184a1` | Bind activity reentrancy to asyncio tasks | Applied |
| 9 | `c4e93bc5c5c0fffb81c55bef6584b75f2e65ef2a` | `d2d12dcce0c33dafeece9817804b5951d5ad0102` | Harden activity admission handoffs | Applied |
| 10 | `d23b6a4eda27364dbe13ff1bcfd597050974006e` | `42e40d51e4602800c8dd077d6c549800bd0fd2db` | Harden activity shutdown and cancellation lifetimes | Applied |
| 11 | `11b3a07e250c044d2f4c3a082c3244bdbca06362` | `d23df082936796d5f63a4839a46993eee129fab7` | Fix Core upload cancellation and scheduler shutdown race | Applied |
| 12 | `5d651cb42f1b30e24ff56108b64c0d873cfe8ae0` | `f9d370467cb4b884e14c781de5f2000c461b7cf7` | Fix union ordering in upload cancellation types | Applied |
| 13 | `311feca65172379065ac9d7958dd5ede1c890cd7` | `d8f2e6c3ef6b6d5d859fcfec7d3075cf97e1e0ed` | Harden dogfood runtime recovery | Applied with conflict resolution |
| 14 | `bfb662588361d2e0841300cc2699652f6f32aff9` | `05141b923d6e6c3067926740cb56296c6ac8a27f` | Fix SQLite contention classification and Codex read tools | Applied |
| 15 | `ca3e603b86e238ab410c17ab3abf97330ce78ef3` | `3c1d0dfbdbe04d39d023dda8a39a45a6d7de748b` | Harden dogfood runtime readiness | Applied with conflict resolution |
| 16 | `50369053417af53764500ac3b643570e34b51aa9` | `45542d4ebb115064415f5d5843d860ed09440332` | Contain runtime readiness failures | Applied |
| 17 | `bd858d408d56826afdd0b8f435c7cbeb093a86c0` | `b0913a90fb172936727e7c6d77734cadd69a4e0b` | Harden runtime readiness lifecycle recovery | Applied with integrated context |
| 18 | `642fef993c62d287ce189378d7fc143d07771952` | `1b3d3e21f63c3519bef486103dd25efa4caf0f21` | Make Windows application registration reversible | Applied |
| 19 | `e87bd317adb4f4416804c118145378248c2ef102` | `5162d1ad038e92b7eff2a2b286c5c8077762f3d2` | Harden Windows registration recovery and ownership | Applied |
| 20 | `dc9c39fba240d9ac1ccb1c3a3ba2d684b0fcc766` | `7ee6841348730ce34752bf2ea23b124ddde5a179` | Harden Windows application registration recovery | Applied |
| 21 | `08795695d38f416331fbca6e63beb0abe7e4ea03` | `f0bf33ed14c08466893b5703b10a7ec03483de8f` | Harden Windows registration journal recovery | Applied |
| 22 | `8f7ab9363f5fe80155fd3261dc8f8332ee9779cb` | `b6ac360ffe15310287c3cd44ee095001f29c56cb` | Harden Windows registration recovery boundaries | Applied |
| 23 | `bfbd5955df7e2f035dbf75706c57d54bb81dafe5` | `c83e3b39a31b9436a29ee761d92f814b0e6585e7` | Harden Windows registration recovery boundaries | Applied |
| 24 | `92f0c14b2e00e2e9cf23989ddef754a86f5f1040` | `9561dc95f7a20604168b5a8cc195bca4bc643605` | Harden Windows registration restart recovery | Applied |
| 25 | `3aa1271b3740d5b89378a15ed7dd13815ed578d9` | `d81a9dee608c2b5fdce9e66147a3e5f867be8c0c` | Repair stock Windows registration fallback | Applied |
| 26 | `f86e1e48feed49a8fd3f188e70519092f1a372bb` | `2c4a547da333476d2a9049f4f53063a14be69f38` | Repair atomic Windows registry publication | Applied |
| 27 | `d8074cd8e1798e73d1d212f47a08a8804fe67d2e` | `1a08fe356c25641919abf18c1ddd2209cd160d42` | Harden native registry cleanup ownership | Applied |
| 28 | `741800b46d360a9c463f7813326c8e24e4f35d19` | `a1cf110dd216bfe2ed8eb5121f9b0a62bdb1aa20` | Close native registry publication and cleanup races | Applied |
| 29 | `b336563764ccbb8a43d53debabd1f3fe09169224` | `86adb2693973ab2083abb3aeb360433c708646cf` | Harden Windows registry publication transactions | Applied |
| 30 | `377c5a1c18fc50d89031ca85e5aec530f3b2a616` | `cff33f0082319559485eeac5aa816417eaa5a122` | Harden registry transaction cleanup ownership | Applied |
| 31 | `4e481678792084928601ddacc0109270ab4f6529` | `de823354bfcbbab6a800c00079185dc7fd779538` | Make build identity immutable across packaged runtime | Applied with conflict resolution |
| 32 | `087a1355c3cb40ab645c097d667e7df8ee4cdb7f` | `f8917ed46749d3cf94282b25c2b9b17ff5fa47f3` | Repair packaged update provenance gates | Applied |
| 33 | `8b6f01ba45e384e36f76faaae6cfe2af667b3a84` | `c0327319895cde9607f6f06c887427843f067cb9` | Harden updater replay and beta inventory validation | Applied |
| 34 | `c9a500ee5f05b03aa731eeffb8f34f3b15c748f7` | `0a2d9648ce32c8945ae13aaeee2e70460d3f5810` | Harden packaged terminal replay identity | Applied |
| 35 | `de68f18d9e779d071042681df149624246350d0b` | `a21334385fb3850bef68d2cf22fd27f5ae8fc0b6` | Fix packaged startup terminal replay dispatch | Applied |
| 36 | `f0ad61485a92588a523c7e65a6bd8883230792be` | `f60fd3b8268ca9cbcb18350cf1b7f6585b38b76a` | Harden imported memory admission and supersession | Applied |
| 37 | `18926afd42040ad7f1af573c096a4c258708e22c` | `e1d8f0121b2439c102633f03dc5cadeca726e912` | Repair archive memory hardening | Applied |
| 38 | `92cd56a62aa25d6f5be6ed4f36d475669de7b651` | `956b40f33135b71cd37d1ef22588192ddebf8f71` | Harden archive memory identity and bounded targeting | Applied with conflict resolution |
| 39 | `ffa054de09117734da5897215f579a1344805cbf` | `17f4fcd5df19d00a0af4cab4666a76da171dfb32` | Repair archive slot and legacy reference compatibility | Applied |
| 40 | `c7c9fa01edbdf22995d9905a02989726cd938e43` | `b792520aa07d95e991fa27716d6114d02eb8cb26` | Constrain archive slot fallback identity | Applied |
| 41 | `bf6af4fbffe6420652ddcb978a33f3f7016fabb0` | `e6648ac82bd9b06c65bcc7ecf8a7ca4f607dc25c` | Block stale archive evidence across slot corrections | Applied |
| 42 | `5377adf0b29c39690cc97f3708da87ccacfeb84d` | `4fd092158936f3c0836a49a6c40406ff58e08e9a` | Harden archive purge and supersession barriers | Applied with integrated context |
| 43 | `f44ec98a55ebf44e790060543154edf7c17637dd` | `4aa41ed7adf4e79caf91f922dd86a814b1cd0f3b` | Repair source-less archive replay barriers | Applied |
| 44 | `919694772a6c6ff8fa318d59acf62b73a3912526` | `e5971581e9fe89aa061844154d1cb7ea0a5ea8f5` | Preserve source-less purge barriers in exports | Applied |
| 45 | `7b643bee562ee48f8784237483c00a04b0f75742` | `282f4031ff77edb047b28ba632229c3fe60e362e` | Harden portable restore barrier authenticity | Applied |
| 46 | `58a2410a7e92484a3fd81d0126b7d1ae6dc631e9` | `05f6e85d43957fc374289a99ebc12e8b54fd94b9` | Harden portable export vault binding and versions | Applied |
| 47 | `0968a24dff48cd74c3b3301f0ad1298df6527367` | `4a98c721ea787c3ed3bb483f61f63368f6c2b2f3` | Keep authorization state out of portable exports | Applied |
| 48 | `284e2985131589b98cd48d34f1f1706fd878e6ad` | `fd30dbd108537b3fe94f624322cab3f0384e7895` | Harden restore package graph containment | Applied |
| 49 | `832779b25e1cf5ce79011cec10f5907fb21f3e4e` | `9c0031608deaa5f2243710a2932321409de1ac72` | Scope purge barriers to vault and target identity | Applied with conflict resolution |

## Conflict decisions

- `capture_scheduler.py`: retained the integrated Core activity gate and cycle
  lock while adding transient SQLite contention classification, readiness
  recovery, cycle reporting, and worker lifecycle containment from the source
  lanes.
- Windows installation/desktop/build identity: retained the reversible
  registration transaction and current bootstrap transaction, then added
  packaged build-identity fields and stale-registration refresh checks only
  when the embedded identity requires them.
- Portable export/restore: retained the full graph and purge identity checks;
  machine-local security tables are excluded from exports and skipped when
  validating legacy archives. Portable ACL lists are detached from excluded
  source principals, and legacy capture references are ignored before restore.
  The stale session-export regression was updated to assert this explicit
  boundary.
- Storage/archive: retained both observation safety bounds and both purge
  identity validators, including vault and target identity in restore barriers.
- Test-contract reconciliation: publication fixtures now carry the required
  source-bound build identity, the project-runtime ambiguity fixture remains
  self-contained under archive admission rules, and the Windows path fixture
  uses a neutral absolute root so the committed-tree security scan stays strict.

## Evidence status

Final local source head before this evidence update:
`5c79ada65b912daf40d16be04bb137946296db7b`.

| Gate | Result |
|---|---|
| Full pytest after functional integration (`c8b51a0`) | 3,069 passed, 19 capability skips, 3 warnings; 3,088 collected |
| Post-scan fixture regression at `5c79ada` | 47 passed, 3 capability skips |
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed; 376 files formatted |
| `python -m mypy packages/allthecontext/src` | Passed; 112 source files |
| Docs link check | Passed |
| Actions pin check | Passed; 48 third-party pins |
| Runner architecture check | Passed; Windows `x86_64` |
| Pytest collection parity | Passed; 3,088 node IDs identical sequential/2-worker |
| Repository security tree/history scan | Passed; 598 tree files and 3,514 reachable blobs, zero findings |

No native artifact was built: reproducibility requires Python 3.12.10 and uv
0.11.32, while this host provides Python 3.14.3 and uv 0.12.9. PyInstaller
6.21.0 is present, but the pinned Python/uv gate is not satisfied. Therefore
there are no artifact hashes or exact-artifact results. No artifact was
executed, installed, updated, signed, published, scanned by Defender, or used
for clean-machine acceptance by this integration worker. Hosted-check snapshot
and the single draft PR URL will be appended after the named branch is pushed.
