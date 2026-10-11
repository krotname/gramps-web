# GEDCOM master gateway

This fork uses stable Gramps Web v26.10.0 and Gramps Web API v3.23.1 (Gramps 6.0.8). The canonical format is GEDCOM 5.5.1. Native tree mutations are blocked at the gateway; the owner editor commits the GEDCOM before updating the derived Gramps database. A failed projection stays pending and can be retried. Stable cross-reference IDs and unknown GEDCOM extensions are preserved.

Run the API and gateway in the same isolated pod as non-root processes. Give the API only a loopback listener; expose port 8000 of the gateway. Persist /data/master and /data/gramps. Configure MASTER_ORIGIN, MASTER_DATA, GRAMPS_RUNTIME_DATA, GRAMPS_TREE_NAME, GRAMPS_OWNER_USER and the initial GRAMPS_OWNER_PASSWORD through private runtime configuration. Store the password outside Git. MASTER_FOCUS can set the initial person without embedding private IDs in this public fork. The first API start creates the owner account; restart preserves credentials. Public registration is disabled.

The API command is gunicorn --bind 127.0.0.1:5000 'gateway.runtime:create()'. The gateway command is python -m gateway.server. Initialize the master once with Store.initialize, then use the owner project endpoint. Never replace the master with a subsequent site export. Use imported exports for comparison only. Keep scans, raw OCR, credentials and family data outside this repository.

The editor is in Russian. People, events, family links, notes and sources are saved through the GEDCOM editor. The built-in Gramps views use the derived database. Only the owner/admin can download or mutate the master and use the publication queue.

## Private media

The native API serves files from `GRAMPS_RUNTIME_DATA/media`; remote Geni URLs in a GEDCOM are not downloadable media files for this API. Export the authenticated media API list to a private operator manifest. Run `python -m gateway.media_import --manifest <private-manifest.json> --media-dir <staging-media> --map <staging-map.json>` on the operator machine. The importer accepts bounded HTTPS downloads only from media.geni.com, checks file signatures and transport length, deduplicates repeated references, and reuses valid existing files. It never changes the GEDCOM or prints signed source URLs.

Inspect every staged file and PDF page before transferring it. Act-record copies and their full OCR remain on the operator machine; remove those entries from the approved manifest. Install only the approved files under `GRAMPS_RUNTIME_DATA/media` and the completed map at `MASTER_MEDIA_MAP`. Request the authenticated owner `/api/master/project/` endpoint, then read back every file/thumbnail and the unchanged canonical revision. Renewed Geni URLs use the same local filename. Repeat this procedure for newly added media; a saved URL alone does not prove an available image.

## Publication

The publication button freezes the current GEDCOM revision; ordinary saves do not update external sites. The queue records books and Geni independently. Repeating the request for a revision does not duplicate it. At most ten frozen revisions are retained; unfinished work is never discarded to make room.

Set MASTER_WORKER_CONFIG to a private JSON file to enable the automatic publisher. It contains books and geni as explicit argument arrays, plus timeout_seconds between 5 and 1800. The two hooks start independently from the same immutable snapshot. The hook environment provides GENEALOGY_SNAPSHOT, GENEALOGY_REVISION and GENEALOGY_JOB_DIR; stdout must be a JSON report with the exact canonical_revision. A books report contains six relative PDF paths, page counts and successful checks. A Geni report is ready only with nonempty readback evidence. Hook credentials and project metadata stay outside Git. Reports are limited to 2 MiB and each PDF to 32 MiB. Timeouts terminate the entire hook process group.

A process lock prevents concurrent workers from replaying a request. After interruption, book generation may resume; Geni is blocked until the operator reads back the external result. The worker does not automatically retry an uncertain external write.

An operator uses ops/publication.py with GENEALOGY_API and GENEALOGY_TOKEN in the process environment. The next command reads the oldest unfinished request. The fetch command downloads its immutable snapshot. Build six PDFs on the build server from that snapshot and editorial-only metadata. The accepted build report must contain canonical_revision, six volumes and successful checks. The books command uploads the PDFs and installs an authenticated manifest. A status-only request cannot mark books ready.

For Geni, compare this snapshot with the last confirmed published revision. Apply only targeted profile/family changes through an authorized API or the established operator procedure; do not import the whole tree into the shared Geni tree. Read back each changed profile and record its URLs with the result command. Mark a partial failure blocked or failed. Record ready only after readback proves the intended changes. Site credentials remain on the operator machine.

## Red CI

The verification workflow uses the repository ARC runner and its persistent cache. It lints, builds, typechecks, runs the frontend and gateway tests, and assembles an OCI image from a pinned stable API image without a privileged Docker build. Successful runs publish ghcr.io/krotname/gramps-web:sha-<source-sha>. Deployment should pin the resulting digest. Keep runner registration and deployment policy in the private infrastructure repository.
