# FOSSology — Workflow Docs

## 1) Why this exists (what it’s for)

Use this workflow to run **FOSSology** license compliance scans in CI for any of these inputs:

* **Docker image** (e.g., `alpine:latest`)
* **Git repo** at a **branch / tag / commit**
* **Uploaded archives**: **ZIP** or **TAR** (incl. `.tar.gz/.tgz`)

It produces:

* Official **FOSSology reports**: **SPDX 2.0**, **ReadmeOSS**, **License Texts**, **License List**
* Raw **JSON** from FOSSology endpoints (licenses/copyrights/summary/…)
* Flattened **CSVs** generated from the JSON (easy to filter/grep in Excel)
* A single **artifact ZIP** named with the **input tag** (source label) and the **run id**

> Notes
>
> * CycloneDX is **not** emitted by FOSSology; use our ScanCode/Syft SBOM workflows if you need CycloneDX.
> * We removed **keyword** and **pkgagent** by design; the default agents focus on core license discovery.

---

## 2) How the workflow works (under the hood)

### Inputs (from “Run workflow” form)

* `scan_type`: `docker` | `repo` | `upload-zip` | `upload-tar`
* `docker_image`: image ref (for `docker`)
* `repo_url`: repo URL (for `repo`) **or** file URL (for `upload-*`)
* `repo_ref`: branch / tag / commit (for `repo`)
* Agent toggles (all **default ON**, and **keyword/pkgagent are omitted**):

  * `agent_nomos` – core license scanner
  * `agent_ojo` – extended license scanner (**implies `nomos`** if not set)
  * `agent_monk` – license text detection in archives/binaries
  * `agent_copyright` – copyrights/emails/authors

### End-to-end flow

1. **Prepare input & INPUT\_TAG**

   * **docker**: `docker pull` → `docker save` → `docker-image.tar`
     `INPUT_TAG = <image-ref>`
   * **repo**: normalize URL/ref, try **shallow clone** for branch/tag; else **full clone** + detached checkout at commit.
     Tar up working tree → `repo.tar.gz`
     `INPUT_TAG = <repo-name>_<ref>_<commit12>`
   * **upload-zip/tar**: `curl -L` the archive to runner
     `INPUT_TAG = <file-basename>`
   * `INPUT_TAG` is sanitized for filenames (**SAFE\_INPUT\_TAG**) and used as suffix in all outputs.

2. **Start FOSSology**

   * Launches container: `fossology/fossology:4.3.0` (mapped to `localhost:8081`)
   * Waits until `/repo/api/v1/version` responds.

3. **Auth**

   * Creates a short-lived API token (`ci-run`, **scope: write**, default expiry 7 days) with the built-in demo creds (`fossy`/`fossy`).
     *For production, replace these via secrets.*

4. **Upload**

   * `POST /uploads` with the prepared file (`docker-image.tar`, `repo.tar.gz`, or downloaded archive)
   * Polls `/uploads/{id}` until it has `folderid`.

5. **Unpack job**

   * `POST /jobs` with `{analysis:{unpack:true}}` on that folder/upload
   * Polls `/jobs/{id}` until `Completed`.

6. **Scan job (agents = your checkboxes)**

   * Builds `analysis` map (API names):

     * `nomos`, `ojo`, `monk`, `copyright_email_author`
   * Sends deciders: `{nomos_monk:true, bulk_reused:true, new_scanner:true}`
   * `reuse` disabled for deterministic CI
   * Polls the scan job status until complete.

7. **Collect quick counts**

   * For each license agent selected, GET `/uploads/{uploadId}/licenses?agent=<agent>&containers=true`
   * Keep simple counts used in the console summary.

8. **Reports (files include SAFE\_INPUT\_TAG)**

   * Request report jobs, poll, then download:

     * `report_spdx2_<TAG>_<TS>.spdx2`
     * `report_readmeoss_<TAG>_<TS>.readmeoss`
     * `report_license_text_<TAG>_<TS>.license_text`
     * `report_license_list_<TAG>_<TS>.license_list`

9. **JSON & CSV (flattened)**

   * Fetch and save raw JSON + CSV (CSV via a generic `jq` flattener):

     * `/uploads/{uploadId}/licenses?agent=nomos, ojo, monk&containers=true` *(only agents you ran)*
     * `/uploads/{uploadId}/copyrights`
     * `/uploads/{uploadId}/decisions`
     * `/uploads/{uploadId}/obligations`
     * `/uploads/{uploadId}/summary`
   * Filenames look like:
     `uploads_<id>_licenses_agent_nomos_ojo_monk_containers_true_<TAG>_<TS>.json/.csv` (and similar for others)

10. **Artifact packaging**

    * Zips everything under `fossology_reports/` into
      **`fossology_reports_<INPUT_TAG>_<GITHUB_RUN_ID>.zip`**
    * Uploads as artifact
      **`fossology-reports-<INPUT_TAG>-<GITHUB_RUN_ID>`**

### Agent logic

* If you enable **OJO** and (accidentally) disable **Nomos**, the workflow **auto-adds Nomos** (OJO depends on it).
* **Keyword** and **PkgAgent** are intentionally **not** exposed; add them back only if you really need keyword hits or package metadata from FOSSology.

### Security & environment notes

* Current script uses demo `fossy/fossy` creds; for real projects, pass **`FOSSOLOGY_URL/USERNAME/PASSWORD`** via encrypted secrets.
* The container runs locally (ephemeral) on the runner; no state is persisted between runs unless you bind a volume (not done here).
* Default token lifetime is 7 days for the API token created per run.

---

## 3) How to use the UI (GitHub Actions → “Run workflow”)

1. **Open the workflow**

   * GitHub → **Actions** → choose **“Fossology final”** (or your file’s name).

2. **Click “Run workflow”** and fill inputs:

   * **Select source**

     * `scan_type = docker` → set `docker_image` (e.g., `eclipse-temurin:17-jre-alpine`)
     * `scan_type = repo` → set `repo_url` and optional `repo_ref` (branch/tag/commit)

       * You can paste real GitHub **/tree/<ref>** or **/releases/tag/<tag>** links; the script handles both shallow and detached checkouts.
     * `scan_type = upload-zip` or `upload-tar` → set `repo_url` to a **direct file URL** (public release asset or HTTP/S link).
   * **Agents**

     * `nomos`, `ojo`, `monk`, `copyright`
     * Defaults are **all ON**; **ojo** implies **nomos** automatically.

3. **Run** and watch logs

   * You’ll see input prep, container startup, auth, upload id/folder id, unpack and scan job polling, and report/JSON fetch messages.
   * The console ends with a **Scan Summary** table (agent → findings count).

4. **Download your results**

   * In the run page, under **Artifacts**, download
     **`fossology-reports-<INPUT_TAG>-<RUN_ID>`** (ZIP).
   * Inside you’ll find:

     * **Official reports**: `report_spdx2_…`, `report_readmeoss_…`, `report_license_text_…`, `report_license_list_…`
     * **Raw JSON** & **CSV** for licenses, copyrights,
       decisions, obligations, summary (per your agents)
     * A text summary in logs you can copy into your release notes if needed

### Tips & troubleshooting

* **“Value not in list” (422) on dispatch**
  Ensure `scan_type` matches exactly one of: `docker | repo | upload-zip | upload-tar` in both UI and workflow file.
* **Artifact 403 when clicking raw API link**
  Download artifacts via the GitHub UI **Artifacts** panel; API access can require extra **actions** scope.
* **Empty/low findings**

  * For `repo`, confirm `repo_ref` exists. If it’s a commit SHA, detached checkout is used—works fine.
  * For `upload-*`, verify the URL is a direct download (some sites require auth or redirect to HTML).
* **Need CycloneDX?**
  FOSSology doesn’t emit CycloneDX. Use our **ScanCode Toolkit** or **SCANOSS/Syft** workflows to generate CycloneDX alongside FOSSology.
* **Performance**
  Big images/repos take longer; the workflow already polls jobs and waits for report readiness with backoffs.
