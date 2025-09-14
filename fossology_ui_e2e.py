import re
import time
import base64
import json
from datetime import datetime, timedelta

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
# UI SETUP
# ===============
st.set_page_config(page_title="Fossology Scan Runner", layout="wide")
st.title("🧩 Fossology Scan Runner – Full UI")
st.caption("Trigger the **FOSSology** workflow for Docker images, Git repos, or uploaded archives (ZIP/TAR). Includes tag/branch loader, run links, status polling, and artifact downloads.")

# ===============
# HELPERS
# ===============
GH_RE = re.compile(
    r"https?://github\\.com/(?P<owner>[^/]+)/(?P<repo>[^/\\.#]+)(?:\\.git)?"
    r"(?:/(?:tree|releases/tag|commit)/(?P<ref>[^/?#]+))?",
    re.IGNORECASE,
)


def gh_ok() -> bool:
    return bool(TOKEN)


def api_get(url: str, **kwargs):
    r = requests.get(url, headers=HEADERS, **kwargs)
    return r


def api_post(url: str, json_data: dict):
    r = requests.post(url, headers=HEADERS, json=json_data)
    return r


def api_put(url: str, json_data: dict):
    r = requests.put(url, headers=HEADERS, json=json_data)
    return r


def normalize_repo(url: str, ref_input: str):
    """Return (canon_git_url, ref, meta)
    - Ensures https://github.com/<owner>/<repo>.git
    - Extracts ref if user pasted a web URL (tree/tag/commit)
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
    """Best-effort prediction of the input tag used in workflow filenames."""
    if scan_type == "docker":
        return sanitize_tag(docker_image)
    if scan_type == "repo":
        # Try to get short SHA for branch/tag via refs. If SHAs can't be resolved here, just use repo+ref.
        m = GH_RE.match(repo_url or "")
        repo_name = (m.group("repo") if m else (repo_url.rsplit("/", 1)[-1].replace(".git", "")))
        owner = m.group("owner") if m else OWNER
        short = None
        # Try heads first
        r = api_get(f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/heads/{repo_ref}")
        if r.status_code == 200:
            sha = r.json().get("object", {}).get("sha") or r.json().get("sha")
            if sha:
                short = sha[:12]
        else:
            # Try tags
            r2 = api_get(f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/tags/{repo_ref}")
            if r2.status_code == 200:
                sha = r2.json().get("object", {}).get("sha") or r2.json().get("sha")
                if sha:
                    short = sha[:12]
        if not short and re.fullmatch(r"[0-9a-fA-F]{7,40}", repo_ref or ""):
            short = (repo_ref or "")[:12]
        tag = f"{repo_name}_{repo_ref}_{short}" if short else f"{repo_name}_{repo_ref}"
        return sanitize_tag(tag)
    if scan_type in ("upload-zip", "upload-tar"):
        base = file_url_filename or (repo_url.rsplit("/", 1)[-1] if repo_url else "file")
        base = re.sub(r"\.(zip|tar|gz|tgz)$", "", base, flags=re.IGNORECASE)
        return sanitize_tag(base)
    return "input"


def dispatch_workflow(inputs: dict):
    url = f"{API_BASE}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    payload = {"ref": BRANCH, "inputs": inputs}
    r = api_post(url, payload)
    return r


def find_recent_run(workflow_file: str, created_after: datetime):
    # List runs for a workflow file and return the most recent after a timestamp
    url = f"{API_BASE}/actions/workflows/{workflow_file}/runs"
    r = api_get(url, params={"per_page": 20})
    if r.status_code != 200:
        return None
    runs = r.json().get("workflow_runs", [])
    for run in runs:
        created_at = datetime.fromisoformat(run.get("created_at").replace("Z", "+00:00"))
        if created_at >= created_after - timedelta(seconds=5):
            return run
    return runs[0] if runs else None


def get_run(run_id: int):
    url = f"{API_BASE}/actions/runs/{run_id}"
    return api_get(url)


def get_run_artifacts(run_id: int):
    url = f"{API_BASE}/actions/runs/{run_id}/artifacts"
    return api_get(url)


def upload_blob_to_repo(bytes_data: bytes, filename: str) -> str:
    """Upload a file into this repo under uploads/<timestamp>/ and return the raw download URL."""
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
    data = r.json()
    download_url = data.get("content", {}).get("download_url")
    return download_url or ""


# =========================
# SIDEBAR CONFIG
# =========================
st.sidebar.header("⚙️ Settings")
st.sidebar.text_input("Owner", value=OWNER, key="owner")
st.sidebar.text_input("Repo", value=REPO, key="repo")
st.sidebar.text_input("Branch", value=BRANCH, key="branch")
st.sidebar.text_input("Workflow file", value=WORKFLOW_FILE, key="wf_file")

if not gh_ok():
    st.sidebar.error("GitHub token missing. Add GITHUB_TOKEN to .streamlit/secrets.toml")
else:
    st.sidebar.success("GitHub token detected")

# =========================
# MAIN FORM
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
input_placeholder = st.empty()

# Dynamic blocks
repo_url = ""
repo_ref = "main"
docker_image = "alpine:latest"
file_url = ""
file_uploader_bytes = None

if scan_type == "docker":
    docker_image = st.text_input("Docker image (e.g., nginx:latest)", value="alpine:latest")
elif scan_type == "repo":
    repo_url = st.text_input("Repo URL", value="https://github.com/example/repo.git")
    repo_ref = st.text_input("Branch / tag / commit", value="main")
    norm_git, norm_ref, meta = normalize_repo(repo_url, repo_ref)
    st.caption(f"Normalized repo: `{norm_git}` | Ref: `{norm_ref}`")
    if meta:
        with st.expander("🔎 Load branches & tags from GitHub", expanded=False):
            if st.button("Load branches & tags"):
                with st.spinner("Fetching branches & tags..."):
                    branches, tags = list_refs(meta["owner"], meta["repo"])
                st.success(f"Loaded {len(branches)} branches, {len(tags)} tags")
                select1, select2 = st.columns(2)
                with select1:
                    if branches:
                        b_sel = st.selectbox("Branches", [norm_ref] + [b for b in branches if b != norm_ref])
                        repo_ref = b_sel
                with select2:
                    if tags:
                        t_sel = st.selectbox("Tags", tags)
                        if st.button("Use selected tag"):
                            repo_ref = t_sel
                st.info("You can also paste a raw commit SHA in the Ref box above.")
            else:
                st.caption("Click to load refs from the remote repo")
elif scan_type in ("upload-zip", "upload-tar"):
    up_col1, up_col2 = st.columns([3,2])
    with up_col1:
        file_url = st.text_input("File URL (public or raw GitHub URL)")
        st.caption("Optionally upload a file below to this repo and auto-generate a URL")
    with up_col2:
        uploaded = st.file_uploader("Upload a file (ZIP/TAR)", type=["zip", "tar", "gz", "tgz"])  # gz/tgz for tarballs
        if uploaded is not None:
            file_uploader_bytes = uploaded.read()
            st.write(f"Selected: {uploaded.name} ({len(file_uploader_bytes)} bytes)")
            if gh_ok() and st.button("Upload file to repo & fill URL"):
                try:
                    with st.spinner("Uploading to repo ..."):
                        url = upload_blob_to_repo(file_uploader_bytes, uploaded.name)
                    if url:
                        file_url = url
                        st.success("Uploaded. URL filled above.")
                        st.session_state["_file_url_prefill"] = url
                except Exception as e:
                    st.error(f"Upload failed: {e}")
    # If uploaded and URL is empty, try to use prefill
    if "_file_url_prefill" in st.session_state and not file_url:
        file_url = st.session_state["_file_url_prefill"]

# Predict input tag preview
pred = predict_input_tag(scan_type, docker_image, repo_url, repo_ref, (uploaded.name if scan_type in ("upload-zip", "upload-tar") and uploaded else ""))
st.info(f"**Expected filename tag:** `{pred}`  (used to suffix report files & artifact name)")

# =========================
# DISPATCH
# =========================
st.subheader("2) Dispatch Workflow")

# Build inputs mapping to the workflow
inputs_payload = {
    "scan_type": scan_type,
    "docker_image": docker_image if scan_type == "docker" else "",
    "repo_url": (repo_url or file_url) if scan_type in ("repo", "upload-zip", "upload-tar") else "",
    "repo_ref": repo_ref if scan_type == "repo" else "",
    "agent_nomos": str(agent_nomos).lower(),
    "agent_ojo": str(agent_ojo).lower(),
    "agent_monk": str(agent_monk).lower(),
    "agent_copyright": str(agent_copyright).lower(),
}

col_run1, col_run2 = st.columns([1,4])
run_clicked = False
with col_run1:
    if st.button("▶️ Run Scan", disabled=not gh_ok()):
        run_clicked = True

run_data_placeholder = st.empty()

if run_clicked:
    if not gh_ok():
        st.error("GitHub token missing. Cannot dispatch.")
    else:
        with st.spinner("Dispatching workflow..."):
            r = dispatch_workflow(inputs_payload)
        if r.status_code in (201, 204):
            st.success("Workflow dispatch accepted ✨")
            st.session_state["dispatch_time"] = datetime.utcnow()
            st.session_state["dispatched_inputs"] = inputs_payload
            st.session_state["workflow_file"] = st.sidebar.session_state.get("wf_file", WORKFLOW_FILE)
        else:
            st.error(f"Dispatch failed: {r.status_code} {r.text}")

# =========================
# STATUS & ARTIFACTS
# =========================
st.subheader("3) Status & Results")

if "dispatch_time" in st.session_state:
    wf_file = st.session_state["workflow_file"]
    since = st.session_state["dispatch_time"]

    col_stat, col_ctrl = st.columns([3,1])
    with col_ctrl:
        auto = st.toggle("Auto-refresh", value=False, help="Refresh status every ~5s")

    if auto:
        # trigger rerun in a tiny loop via empty spinner
        st.experimental_set_query_params(_=int(time.time()))

    with st.spinner("Fetching latest run..."):
        run = find_recent_run(wf_file, since)

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

        # Artifacts
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
                    download_url = a.get("archive_download_url")
                    st.write(f"• **{name}** — {size_in_bytes} bytes | Expired: {expired}")
                    if download_url:
                        st.markdown(f"[Download ZIP]({download_url})")
        else:
            st.error(f"Failed to list artifacts: {art_resp.status_code} {art_resp.text}")

        # Quick table of recent runs for this workflow
        st.markdown("#### Recent runs for this workflow")
        r2 = api_get(f"{API_BASE}/actions/workflows/{wf_file}/runs", params={"per_page": 10})
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
    "• Repo ref can be a branch, tag, or commit. Use the loader to browse refs. "
    "• For upload-* types, you can either paste a URL or upload a file directly to this repo (auto URL). "
    "• Artifacts are named with an input tag (e.g., repo_ref/commit or docker image) and the run id."
)
