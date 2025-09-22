import re
import time
import base64
from datetime import datetime, timedelta, timezone  # timezone added

import requests
import streamlit as st

# =========================
# CONFIG (edit if needed)
# =========================
OWNER = "Bharathnelle335"          # ➜ Your GitHub username/org
REPO = "Fossology_Workflow"        # ➜ Repo that contains the workflow file
BRANCH = "main"                    # ➜ Branch to dispatch on
WORKFLOW_FILE = "fossology.yml"  # ➜ Exact workflow filename in the repo

# Token is expected from Streamlit secrets
# Create .streamlit/secrets.toml with:  GITHUB_TOKEN = "ghp_xxx"
TOKEN = st.secrets.get("GITHUB_TOKEN", "")

API_BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}

# ===============
# UI SETUP (NO SIDEBAR)
# ===============
st.set_page_config(page_title="Fossology Scan Runner", layout="wide")
st.title("Fossology Scanner")
st.caption("© EY Internal Use Only")
st.caption("Trigger the **FOSSology** workflow for Docker images, Git repos, or uploaded archives (ZIP/TAR). Includes **Load Tags**, run links, status polling, and tokened artifact downloads.")

if not TOKEN:
    st.error("GitHub token missing. Add GITHUB_TOKEN to .streamlit/secrets.toml (fine-grained: Actions=Read).")

# ===============
# HELPERS
# ===============
GH_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\.#]+)(?:\.git)?"
    r"(?:/(?:tree|releases/tag|commit)/(?P<ref>[^/?#]+))?",
    re.IGNORECASE,
)

def api_get(url: str, **kwargs):
    return requests.get(url, headers=HEADERS, **kwargs)

def api_post(url: str, json_data: dict):
    return requests.post(url, headers=HEADERS, json=json_data)

def api_put(url: str, json_data: dict):
    return requests.put(url, headers=HEADERS, json=json_data)

def normalize_repo(url: str, ref_input: str):
    """
    Return (canon_git_url, ref, meta)
    - Ensures https://github.com/<owner>/<repo>.git
    - Extracts ref if user pasted a web URL (tree/tag/commit).
    - Fallback to provided ref_input if not present in URL.
    """
    m = GH_RE.match(url.strip())
    if not m:
        return url, ref_input, {}
    owner = m.group("owner")
    repo = m.group("repo")
    ref = m.group("ref") or ref_input or "main"
    canon = f"https://github.com/{owner}/{repo}.git"
    return canon, ref, {"owner": owner, "repo": repo}

def list_refs(owner: str, repo: str):
    branches = []
    tags = []
    # Branches
    page = 1
    while True:
        r = api_get(f"https://api.github.com/repos/{owner}/{repo}/branches", params={"per_page": 100, "page": page})
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        branches.extend([b.get("name") for b in data])
        page += 1
    # Tags
    page = 1
    while True:
        r = api_get(f"https://api.github.com/repos/{owner}/{repo}/tags", params={"per_page": 100, "page": page})
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        tags.extend([t.get("name") for t in data])
        page += 1
    return branches, tags

def sanitize_tag(s: str) -> str:
    s = re.sub(r"[\s/:@#?&]", "-", s)
    s = re.sub(r"[^A-Za-z0-9._-]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")

def predict_input_tag(scan_type: str, docker_image: str, repo_url: str, repo_ref: str, file_url_filename: str = ""):
    if scan_type == "docker":
        return sanitize_tag(docker_image)
    if scan_type == "repo":
        m = GH_RE.match(repo_url or "")
        repo_name = (m.group("repo") if m else (repo_url.rsplit("/", 1)[-1].replace(".git", "")))
        return sanitize_tag(f"{repo_name}_{repo_ref}")
    if scan_type in ("upload-zip", "upload-tar"):
        base = file_url_filename or (repo_url.rsplit("/", 1)[-1] if repo_url else "file")
        base = re.sub(r"\.(zip|tar|gz|tgz)$", "", base, flags=re.IGNORECASE)
        return sanitize_tag(base)
    return "input"

def dispatch_workflow(inputs: dict):
    url = f"{API_BASE}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    payload = {"ref": BRANCH, "inputs": inputs}
    return api_post(url, payload)

def find_recent_run(workflow_file: str, created_after: datetime):
    url = f"{API_BASE}/actions/workflows/{workflow_file}/runs"
    r = api_get(url, params={"per_page": 20})
    if r.status_code != 200:
        return None
    runs = r.json().get("workflow_runs", [])
    for run in runs:
        # make created_at timezone-aware (UTC)
        created_at = datetime.fromisoformat(
            run.get("created_at").replace("Z", "+00:00")
        ).astimezone(timezone.utc)

        # normalize created_after to UTC-aware
        created_after_utc = (
            created_after.replace(tzinfo=timezone.utc)
            if created_after.tzinfo is None
            else created_after.astimezone(timezone.utc)
        )

        if created_at >= created_after_utc - timedelta(seconds=5):
            return run
    return runs[0] if runs else None

def get_run(run_id: int):
    return api_get(f"{API_BASE}/actions/runs/{run_id}")

def get_run_artifacts(run_id: int):
    return api_get(f"{API_BASE}/actions/runs/{run_id}/artifacts")

# NEW: tokened artifact fetch (avoids 403 when clicking raw URL)
def fetch_artifact_zip(artifact_id: int) -> bytes | None:
    """
    Download artifact ZIP via API using the Authorization header.
    Requires PAT with Actions: Read (fine-grained) or a classic PAT with repo scope.
    """
    url = f"{API_BASE}/actions/artifacts/{artifact_id}/zip"
    r = api_get(url)  # requests follows redirects; headers are included
    if r.status_code == 200:
        return r.content
    st.error(f"Artifact download failed: {r.status_code} {r.text}")
    return None

def upload_blob_to_repo(bytes_data: bytes, filename: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = f"uploads/{ts}/{filename}"
    url = f"{API_BASE}/contents/{path}"
    payload = {
        "message": f"Add upload asset {filename}",
        "content": base64.b64encode(bytes_data).decode("utf-8"),
        "branch": BRANCH,
    }
    r = api_put(url, payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed: {r.status_code} {r.text}")
    return r.json().get("content", {}).get("download_url", "")

# =========================
# MAIN FORM (NO SIDEBAR)
# =========================
st.subheader("1) Choose Input & Options")

col1, col2 = st.columns([1,1])
with col1:
    scan_type = st.selectbox("Scan Type", ["docker", "repo", "upload-zip", "upload-tar"], index=0)
with col2:
    st.markdown("**Agents (default ON)** – keyword & pkgagent are omitted by design")
    agent_nomos = st.checkbox("nomos", value=True)
    agent_ojo = st.checkbox("ojo", value=True)
    agent_monk = st.checkbox("monk", value=True)
    agent_copyright = st.checkbox("copyright", value=True)

# Input sections
repo_url = ""
repo_ref = "main"
docker_image = "alpine:latest"
file_url = ""
file_uploader_bytes = None
uploaded_name = ""

if scan_type == "docker":
    docker_image = st.text_input("Docker image (e.g., nginx:latest)", value="alpine:latest")

elif scan_type == "repo":
    repo_url = st.text_input("Repo URL", value="https://github.com/example/repo.git")
    repo_ref = st.text_input("Branch / tag / commit", value="main")
    norm_git, norm_ref, meta = normalize_repo(repo_url, repo_ref)
    st.caption(f"Normalized repo: `{norm_git}` | Ref: `{norm_ref}`")

    # ===== Prominent LOAD TAGS / LOAD BRANCHES controls (no sidebar) =====
    if meta:
        btn_cols = st.columns([1,1,2])
        with btn_cols[0]:
            load_tags = st.button("🔖 Load Tags", use_container_width=True)
        with btn_cols[1]:
            load_branches = st.button("🌿 Load Branches", use_container_width=True)

        # Keep results in session for this owner/repo
        key_b = f"refs_br_{meta['owner']}_{meta['repo']}"
        key_t = f"refs_tg_{meta['owner']}_{meta['repo']}"
        if load_tags or load_branches:
            with st.spinner("Fetching refs from GitHub..."):
                branches, tags = list_refs(meta["owner"], meta["repo"])
            st.session_state[key_b] = branches
            st.session_state[key_t] = tags

        branches = st.session_state.get(key_b, [])
        tags = st.session_state.get(key_t, [])

        if tags:
            tcol1, tcol2 = st.columns([3,1])
            with tcol1:
                t_sel = st.selectbox("Tags", options=tags)
            with tcol2:
                if st.button("Use Tag"):
                    repo_ref = t_sel
                    st.success(f"Using tag: {repo_ref}")
        if branches:
            bcol1, bcol2 = st.columns([3,1])
            with bcol1:
                b_sel = st.selectbox("Branches", options=branches)
            with bcol2:
                if st.button("Use Branch"):
                    repo_ref = b_sel
                    st.success(f"Using branch: {repo_ref}")

elif scan_type in ("upload-zip", "upload-tar"):
    up_col1, up_col2 = st.columns([3,2])
    with up_col1:
        file_url = st.text_input("File URL (public or raw GitHub URL)")
        st.caption("Optionally upload a file below to this repo and auto-generate a URL")
    with up_col2:
        uploaded = st.file_uploader("Upload a file (ZIP/TAR)", type=["zip", "tar", "gz", "tgz"])  # gz/tgz for tarballs
        if uploaded is not None:
            file_uploader_bytes = uploaded.read()
            uploaded_name = uploaded.name
            st.write(f"Selected: {uploaded.name} ({len(file_uploader_bytes)} bytes)")
            if TOKEN and st.button("Upload file to repo & fill URL"):
                try:
                    with st.spinner("Uploading to repo ..."):
                        url = upload_blob_to_repo(file_uploader_bytes, uploaded.name)
                    if url:
                        file_url = url
                        st.success("Uploaded. URL filled above.")
                        st.session_state["_file_url_prefill"] = url
                except Exception as e:
                    st.error(f"Upload failed: {e}")
    if "_file_url_prefill" in st.session_state and not file_url:
        file_url = st.session_state["_file_url_prefill"]

# Predict input tag preview
pred = predict_input_tag(scan_type, docker_image, repo_url if scan_type == "repo" else file_url, repo_ref, uploaded_name)
st.info(f"**Expected filename tag:** `{pred}`  (used to suffix report files & artifact name)")

# =========================
# DISPATCH
# =========================
st.subheader("2) Dispatch Workflow")

inputs_payload = {
    "scan_type": scan_type,
    "docker_image": docker_image if scan_type == "docker" else "",
    "repo_url": (repo_url or file_url) if scan_type in ("repo", "upload-zip", "upload-tar") else "",
    "repo_ref": repo_ref if scan_type == "repo" else "",
    "agent_nomos": str(True).lower(),
    "agent_ojo": str(True).lower(),
    "agent_monk": str(True).lower(),
    "agent_copyright": str(True).lower(),
}

run_clicked = st.button("▶️ Run Scan", disabled=not TOKEN)

if run_clicked:
    if not TOKEN:
        st.error("GitHub token missing. Cannot dispatch.")
    else:
        with st.spinner("Dispatching workflow..."):
            r = dispatch_workflow(inputs_payload)
        if r.status_code in (201, 204):
            st.success("Workflow dispatch accepted ✨")
            st.session_state["dispatch_time"] = datetime.now(timezone.utc)  # timezone-aware
        else:
            st.error(f"Dispatch failed: {r.status_code} {r.text}")

# =========================
# STATUS & ARTIFACTS
# =========================
st.subheader("3) Status & Results")

if "dispatch_time" in st.session_state:
    since = st.session_state["dispatch_time"]
    col_stat, col_ctrl = st.columns([3,1])
    with col_ctrl:
        auto = st.toggle("Auto-refresh", value=False, help="Refresh status every ~5s")
    if auto:
        st.query_params["_"] = str(int(time.time()))

    with st.spinner("Fetching latest run..."):
        run = find_recent_run(WORKFLOW_FILE, since)

    if not run:
        st.warning("No run found yet. It may take a few seconds to appear.")
    else:
        run_id = run.get("id")
        html_url = run.get("html_url")
        status = run.get("status")
        conclusion = run.get("conclusion")
        created_at = run.get("created_at")
        updated_at = run.get("updated_at")

        with col_stat:
            st.markdown(f"**Run:** [{html_url}]({html_url})")
            st.write(f"**Status:** {status}  |  **Conclusion:** {conclusion}")
            st.caption(f"Created: {created_at}  |  Updated: {updated_at}")

        # Artifacts (tokened downloads)
        art_resp = get_run_artifacts(run_id)
        if art_resp.status_code == 200:
            artifacts = art_resp.json().get("artifacts", [])
            if not artifacts:
                st.info("No artifacts yet. They appear after the job finishes the 'Upload Artifact' step.")
            else:
                st.markdown("### 📦 Artifacts")
                for a in artifacts:
                    name = a.get("name")
                    size_in_bytes = a.get("size_in_bytes")
                    expired = a.get("expired")
                    artifact_id = a.get("id")

                    st.write(f"• **{name}** — {size_in_bytes} bytes | Expired: {expired}")

                    fetch_key = f"fetch_{artifact_id}"
                    buf_key = f"artifact_bytes_{artifact_id}"

                    if st.button("Fetch", key=fetch_key):
                        with st.spinner("Downloading artifact..."):
                            content = fetch_artifact_zip(artifact_id)
                        if content:
                            st.session_state[buf_key] = content
                            st.success("Ready to download")

                    if buf_key in st.session_state:
                        st.download_button(
                            "Download ZIP",
                            data=st.session_state[buf_key],
                            file_name=f"{name}.zip",
                            mime="application/zip",
                            key=f"dl_{artifact_id}"
                        )
        else:
            st.error(f"Failed to list artifacts: {art_resp.status_code} {art_resp.text}")

        # Recent runs table
        st.markdown("#### Recent runs for this workflow")
        r2 = api_get(f"{API_BASE}/actions/workflows/{WORKFLOW_FILE}/runs", params={"per_page": 10})
        if r2.status_code == 200:
            data = r2.json().get("workflow_runs", [])
            rows = []
            for rr in data:
                rows.append({
                    "id": rr.get("id"),
                    "event": rr.get("event"),
                    "status": rr.get("status"),
                    "conclusion": rr.get("conclusion"),
                    "created_at": rr.get("created_at"),
                    "run_url": rr.get("html_url"),
                })
            st.dataframe(rows, use_container_width=True)
        else:
            st.error(f"Could not fetch recent runs: {r2.status_code}")

# =========================
# FOOTER
# =========================
st.divider()
st.caption(
    "Notes: • The workflow supports scan types: docker, repo, upload-zip, upload-tar. "
    "• Use **Load Tags** to quickly pick a release tag. • Artifacts are fetched via your token and offered as a ZIP download."
)
