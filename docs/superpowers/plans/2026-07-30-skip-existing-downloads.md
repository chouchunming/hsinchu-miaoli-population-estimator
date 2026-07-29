# Skip Existing Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make monthly updates download only attachment URLs that are absent from the local artifact repository.

**Architecture:** Source discovery remains online. The repository exposes the latest locally archived artifact for an exact download URL; the service skips terminal artifacts and reparses failed artifacts from immutable local bytes.

**Tech Stack:** Python 3.14 standard library, SQLite, `unittest`

## Global Constraints

- Deduplicate only by complete `download_url`, never by month.
- Never redownload a URL already present in `artifacts`.
- Reparse failed artifacts from archived bytes.
- Keep current-candidate fail-closed publication behavior.
- Do not commit or push without separate user authorization.

---

### Task 1: Repository Lookup and Update Download Policy

**Files:**
- Modify: `exam_population/repository.py`
- Modify: `exam_population/service.py`
- Test: `tests/test_service.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `PopulationRepository.database_path`, `ArtifactMetadata`, `StoredArtifact`
- Produces: `PopulationRepository.stored_artifact_for_download(download_url: str) -> StoredArtifact | None`

- [x] **Step 1: Write failing service tests**

Change the second-update test so the second call keeps artifact, normalized-row,
fetch-event, and HTTP-call counts unchanged. Add a parser-upgrade test that
patches `_parse` to fail once while saving valid bytes, then verifies the next
update succeeds from local raw without another HTTP call.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
caffeinate -i -m python3 -m unittest \
  tests.test_service.ServiceTests.test_second_update_skips_all_existing_download_urls \
  tests.test_service.ServiceTests.test_second_update_reparses_failed_artifact_from_raw -v
```

Expected: FAIL because current candidates are fetched again and failed artifacts
have no local lookup path.

- [x] **Step 3: Add repository reconstruction**

Query the newest artifact row for an exact `download_url`, resolve its relative
`archive_path` against the database parent, rebuild `ArtifactMetadata`, and
return `StoredArtifact(metadata, path, sha256)`.

- [x] **Step 4: Implement service decision**

For each candidate:

```python
if repo.has_terminal_download(candidate.download_url):
    continue
stored = repo.stored_artifact_for_download(candidate.download_url)
if stored is None:
    data = source.http.get(candidate.download_url)
    stored = archive.store(_metadata(candidate), data)
else:
    data = stored.path.read_bytes()
```

Keep the existing parse, failure-recording, ingestion, and current adoption
logic after this branch.

- [x] **Step 5: Run focused and full tests**

Run:

```bash
caffeinate -i -m python3 -W error::ResourceWarning -m unittest \
  tests.test_service tests.test_storage -v
caffeinate -i -m python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -v
```

Expected: all tests pass with no warnings.

---

### Task 2: Documentation and Live No-Download Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md`

**Interfaces:**
- Consumes: the Task 1 update behavior
- Produces: documented URL-level deduplication and same-URL overwrite trade-off

- [x] **Step 1: Update documentation**

State that every run still discovers official index pages, downloads only
previously unseen attachment URLs, reparses failed raw locally, and cannot
detect same-URL replacement bytes.

- [x] **Step 2: Verify a copy of existing live data**

Copy `data/population` to a durable temporary directory, record
`artifact_fetches`, run:

```bash
caffeinate -i -m python3 scripts/estimate_exam_population.py update \
  --data-root <temporary-copy> --backfill-from 114-01
```

Then confirm `artifact_fetches` is unchanged, the command returns zero, and a
new export was created.

- [x] **Step 3: Run final gates**

Run the full suite, `compileall`, and the artifact validator against the
repository data. Expected: all pass; repository working tree contains only the
scoped code, tests, and documentation changes.
